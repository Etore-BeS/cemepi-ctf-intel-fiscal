"""Stage `enrich`: stream every RFB zip through DuckDB and keep only the debtor firms.

Unit = (zip file, key group). For each unit:
  1. the single CSV member is extracted to temp_dir on the drive with a streaming copy that transcodes it from the
     RFB's Latin-1/cp1252 to UTF-8 (bounded RAM; size checked against the zip directory; see encoding.py) and is reused
     by every key group of that zip; it is deleted when the zip is finished;
  2. DuckDB reads it with read_csv (all VARCHAR, UTF-8), projects ONLY the allowed columns (layouts.KEEP), and
     semi-joins on cnpj_basico against the debtor roots of the group; result -> one Parquet file (atomic rename);
  3. malformed lines are counted (store_rejects) and logged as a number only, never their content.
Code tables (CNAE, natureza, município, motivo, qualificação, país) and the TOM->IBGE table become small Parquets.
DuckDB spills to temp_dir when memory_limit is reached, so a 6 GB CSV never needs to fit in RAM.
"""
from __future__ import annotations

import json
import logging
import shutil
import zipfile
from pathlib import Path

from .collect import keys_glob
from .config import Settings
from .db import connect, q
from .encoding import summarize, transcode
from .layouts import BIG_FILES, CODE_COLS, CODE_FILES, COLS, KEEP, kind_of
from .state import State, run_unit

log = logging.getLogger("cnpj_pipeline")


def _csv(path: Path | list[Path], cols: list[str], encoding: str = "utf-8", header: bool = False,
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


def _marker(out: Path) -> Path:
    return out.with_name(out.name + ".utf8.json")


def _reusable(out: Path, src_size: int, src_id) -> bool:
    """A previous UTF-8 extraction of this exact source, complete (marker written after the atomic rename)."""
    try:
        m = json.loads(_marker(out).read_text())
    except (OSError, ValueError):
        return False
    return (m.get("bytes_in") == src_size and m.get("src_id") == src_id and out.exists()
            and out.stat().st_size == m.get("bytes_out"))


def _materialize(open_src, src_size: int, src_id, out: Path, label: str) -> Path:
    """Stream open_src() to `out` as UTF-8 unless a complete extraction of the same source is there."""
    if _reusable(out, src_size, src_id):
        return out
    out.parent.mkdir(parents=True, exist_ok=True)
    _marker(out).unlink(missing_ok=True)
    out.unlink(missing_ok=True)          # raw (pre-UTF-8) or partial extraction: never two copies on the drive
    tmp = out.with_name(out.name + ".part")
    with open_src() as src, tmp.open("wb") as dst:
        meta = transcode(src, dst)
    if meta["bytes_in"] != src_size or tmp.stat().st_size != meta["bytes_out"]:
        raise RuntimeError(f"{label}: extracted size mismatch")
    tmp.replace(out)
    mtmp = _marker(out).with_suffix(".tmp")
    mtmp.write_text(json.dumps({**meta, "src_id": src_id}))
    mtmp.replace(_marker(out))
    if meta["c1_mapped"] or meta["c1_replaced"]:
        log.warning("%s: %d byte(s) 0x80-0x9F read as cp1252, %d undefined byte(s) -> U+FFFD; no line dropped "
                    "(content not logged)", label, meta["c1_mapped"], meta["c1_replaced"])
    return out


def extract_all(zip_path: Path, dest_dir: Path) -> list[Path]:
    """Stream every file member of `zip_path` to dest_dir as UTF-8 (reused if complete; size checked). Sorted paths."""
    outs = []
    with zipfile.ZipFile(zip_path) as zf:
        infos = sorted((i for i in zf.infolist() if not i.is_dir()), key=lambda i: i.filename)
        if not infos:
            raise RuntimeError(f"{zip_path.name}: no members")
        for info in infos:
            out = dest_dir / Path(info.filename).name
            outs.append(_materialize(lambda info=info: zf.open(info), info.file_size, [info.filename, info.CRC],
                                     out, f"{zip_path.name}:{Path(info.filename).name}"))
    return outs


def to_utf8(path: Path, dest_dir: Path) -> Path:
    """Same as extract_all for a plain CSV (municipios_ibge.csv)."""
    st = path.stat()
    return _materialize(lambda: path.open("rb"), st.st_size, [path.name, st.st_mtime_ns], dest_dir / path.name,
                        path.name)


def encoding_meta(paths: list[Path]) -> dict:
    """Unit meta: totals of the transcoding of these extracted files (from their markers)."""
    return summarize([json.loads(_marker(p).read_text()) for p in paths])


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
            used: list[Path] = []
            for zname, table in CODE_FILES.items():
                zp = s.rfb_dir / zname
                if not zp.exists():
                    log.warning("enrich: %s missing; descriptions for %s will be null", zname, table)
                    continue
                csv = _extract(zp, extract_root / zname)
                used.append(csv)
                con.execute(f"COPY (SELECT DISTINCT trim(codigo) AS codigo, trim(descricao) AS descricao "
                            f"FROM {_csv(csv, CODE_COLS)}) TO {q(tmp / (table + '.parquet'))} (FORMAT PARQUET)")
                n += con.execute(f"SELECT COUNT(*) FROM read_parquet({q(tmp / (table + '.parquet'))})").fetchone()[0]
            ibge = s.rfb_dir / "municipios_ibge.csv"
            if ibge.exists():
                ibge = to_utf8(ibge, extract_root / "municipios_ibge")
                used.append(ibge)
                cols = ["codigo_tom", "codigo_ibge", "nome_tom", "nome_ibge", "uf"]
                con.execute(f"""COPY (SELECT DISTINCT lpad(trim(codigo_tom), 4, '0') AS codigo_tom,
                                             trim(codigo_ibge) AS codigo_ibge, trim(nome_ibge) AS nome_ibge, trim(uf) AS uf
                                      FROM {_csv(ibge, cols, header=True)} WHERE codigo_tom IS NOT NULL)
                                TO {q(tmp / 'municipios_ibge.parquet')} (FORMAT PARQUET)""")
            else:
                log.warning("enrich: municipios_ibge.csv missing; municipio_ibge will be null")
            return n, {"rejects": _rejects(con), "encoding": encoding_meta(used)}
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
                    return n, {"kind": kind, "zip": zp.name, "group": g, "rejects": _rejects(con),
                               "encoding": encoding_meta([csv])}
                run_unit(state, "enrich", unit, final, work, force=force)
            shutil.rmtree(csv.parent, ignore_errors=True)
        rej = sum(__import__("json").loads(m or "{}").get("rejects", 0) for _, st, _, m in state.units("enrich"))
        if rej:
            log.warning("enrich: %d malformed CSV lines skipped in total (content not logged)", rej)
    finally:
        con.close()
