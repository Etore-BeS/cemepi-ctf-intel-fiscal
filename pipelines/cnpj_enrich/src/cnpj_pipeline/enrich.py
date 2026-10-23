"""Stage `enrich`: stream every RFB zip through DuckDB and keep only the debtor firms.

Unit = (zip file, key group). For each unit:
  1. the single CSV member is extracted to temp_dir on the drive with a streaming copy (bounded RAM; size checked
     against the zip directory) and reused by every key group of that zip; it is deleted when the zip is finished;
  2. DuckDB reads it with read_csv (all VARCHAR, Latin-1), projects ONLY the allowed columns (layouts.KEEP), and
     semi-joins on cnpj_basico against the debtor roots of the group; result -> one Parquet file (atomic rename);
  3. malformed lines are counted (store_rejects) and logged as a number only, never their content.
Code tables (CNAE, natureza, município, motivo, qualificação, país) and the TOM->IBGE table become small Parquets.
DuckDB spills to temp_dir when memory_limit is reached, so a 6 GB CSV never needs to fit in RAM.
"""
from __future__ import annotations

import logging
import shutil
import zipfile
from pathlib import Path

from .collect import keys_glob
from .config import Settings
from .db import connect, q
from .layouts import BIG_FILES, CODE_COLS, CODE_FILES, COLS, KEEP, kind_of
from .state import State, run_unit

log = logging.getLogger("cnpj_pipeline")


def _csv(path: Path | list[Path], cols: list[str], encoding: str = "latin-1", header: bool = False,
         delim: str = ";") -> str:
    spec = "{" + ", ".join(f"'{c}': 'VARCHAR'" for c in cols) + "}"
    src = "[" + ", ".join(q(p) for p in path) + "]" if isinstance(path, (list, tuple)) else q(path)
    return (f"read_csv({src}, delim='{delim}', quote='\"', escape='\"', header={str(header).lower()}, "
            f"encoding='{encoding}', columns={spec}, store_rejects=true, strict_mode=false, "
            f"null_padding=false, auto_detect=false, parallel=true)")


def _extract(zip_path: Path, dest_dir: Path) -> Path:
    """Stream the only CSV member of `zip_path` to dest_dir; reuse it if complete. Returns the CSV path."""
    out = extract_all(zip_path, dest_dir)
    if len(out) != 1:
        raise RuntimeError(f"{zip_path.name}: expected 1 member, found {len(out)}")
    return out[0]


def extract_all(zip_path: Path, dest_dir: Path) -> list[Path]:
    """Stream every file member of `zip_path` to dest_dir (reused if complete; size checked). Sorted paths."""
    outs = []
    with zipfile.ZipFile(zip_path) as zf:
        infos = sorted((i for i in zf.infolist() if not i.is_dir()), key=lambda i: i.filename)
        if not infos:
            raise RuntimeError(f"{zip_path.name}: no members")
        for info in infos:
            out = dest_dir / Path(info.filename).name
            outs.append(out)
            if out.exists() and out.stat().st_size == info.file_size:
                continue
            dest_dir.mkdir(parents=True, exist_ok=True)
            tmp = out.with_name(out.name + ".part")
            with zf.open(info) as src, tmp.open("wb") as dst:
                shutil.copyfileobj(src, dst, length=1 << 22)
            if tmp.stat().st_size != info.file_size:
                raise RuntimeError(f"{zip_path.name}: extracted size mismatch")
            tmp.replace(out)
    return outs


def _rejects(con) -> int:
    try:
        n = con.execute("SELECT COUNT(DISTINCT (scan_id, file_id, line)) FROM reject_errors").fetchone()[0]
        con.execute("DELETE FROM reject_errors"); con.execute("DELETE FROM reject_scans")
        return int(n)
    except Exception:  # noqa: BLE001  (no rejects table yet)
        return 0


def run(s: Settings, state: State, files: list[str] | None = None, force: bool = False) -> None:
    state.pin("key_buckets", s.key_buckets)
    if not list(s.collect_keys.glob("kb=*/keys.parquet")):
        raise SystemExit("enrich: no debtor keys; run `collect` first")
    big = [f for f in (files or BIG_FILES) if f in BIG_FILES]
    groups = max(1, int(s.enrich_groups))
    con = connect(s)
    extract_root = s.temp_dir / "extract" / s.ref_rfb
    try:
        # code tables -------------------------------------------------------------------------------------
        def codes_work(tmp: Path) -> tuple[int, dict]:
            tmp.mkdir()
            n = 0
            for zname, table in CODE_FILES.items():
                zp = s.rfb_dir / zname
                if not zp.exists():
                    log.warning("enrich: %s missing; descriptions for %s will be null", zname, table)
                    continue
                csv = _extract(zp, extract_root / zname)
                con.execute(f"COPY (SELECT DISTINCT trim(codigo) AS codigo, trim(descricao) AS descricao "
                            f"FROM {_csv(csv, CODE_COLS)}) TO {q(tmp / (table + '.parquet'))} (FORMAT PARQUET)")
                n += con.execute(f"SELECT COUNT(*) FROM read_parquet({q(tmp / (table + '.parquet'))})").fetchone()[0]
            ibge = s.rfb_dir / "municipios_ibge.csv"
            if ibge.exists():
                cols = ["codigo_tom", "codigo_ibge", "nome_tom", "nome_ibge", "uf"]
                con.execute(f"""COPY (SELECT DISTINCT lpad(trim(codigo_tom), 4, '0') AS codigo_tom,
                                             trim(codigo_ibge) AS codigo_ibge, trim(nome_ibge) AS nome_ibge, trim(uf) AS uf
                                      FROM {_csv(ibge, cols, header=True)} WHERE codigo_tom IS NOT NULL)
                                TO {q(tmp / 'municipios_ibge.parquet')} (FORMAT PARQUET)""")
            else:
                log.warning("enrich: municipios_ibge.csv missing; municipio_ibge will be null")
            return n, {"rejects": _rejects(con)}
        run_unit(state, "enrich", "codes", s.enrich_dir / "codes", codes_work, force=force)

        # big files ---------------------------------------------------------------------------------------
        for zname in big:
            zp = s.rfb_dir / zname
            if not zp.exists():
                raise SystemExit(f"enrich: {zname} not downloaded; run `download` first")
            kind = kind_of(zname)
            stem = zname.removesuffix(".zip")
            units = [(g, f"{zname}#g{g}of{groups}", s.enrich_dir / kind / f"{stem}__g{g}of{groups}.parquet")
                     for g in range(groups)]
            pending = [u for u in units if force or not (state.is_done("enrich", u[1]) and u[2].exists())]
            if not pending:
                log.info("[enrich] %s: all %d group(s) done, skip", zname, groups)
                continue
            csv = _extract(zp, extract_root / stem)
            keep = ", ".join(KEEP[kind])
            for g, unit, final in pending:
                roots = (f"(SELECT DISTINCT cnpj_raiz FROM read_parquet({keys_glob(s)}) "
                         f"WHERE kb(cnpj_raiz, {s.key_buckets}) % {groups} = {g})")

                def work(tmp: Path, csv=csv, roots=roots, kind=kind, keep=keep) -> tuple[int, dict]:
                    con.execute(f"""
                    COPY (SELECT {keep}, kb(cnpj_basico, {s.key_buckets}) AS kb
                          FROM {_csv(csv, COLS[kind])}
                          WHERE cnpj_basico IN {roots}
                    ) TO {q(tmp)} (FORMAT PARQUET, COMPRESSION ZSTD)""")
                    n = con.execute(f"SELECT COUNT(*) FROM read_parquet({q(tmp)})").fetchone()[0]
                    return n, {"kind": kind, "zip": zp.name, "group": g, "rejects": _rejects(con)}
                run_unit(state, "enrich", unit, final, work, force=force)
            shutil.rmtree(csv.parent, ignore_errors=True)
        rej = sum(__import__("json").loads(m or "{}").get("rejects", 0) for _, st, _, m in state.units("enrich"))
        if rej:
            log.warning("enrich: %d malformed CSV lines skipped in total (content not logged)", rej)
    finally:
        con.close()
