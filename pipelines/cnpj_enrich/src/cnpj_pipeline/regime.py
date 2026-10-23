"""Stage `regime`: tax regime (forma de tributação) per CNPJ and year, from the RFB "Regimes Tributários" base (ECF).

Source: Receita Federal, public share arquivos.receitafederal.gov.br (share id in download.REGIME_SHARE), files
entidades-lucro-real.zip, entidades-lucro-presumido.zip (one CSV per year), entidades-lucro-arbitrado.zip,
entidades-imunes-e-isentas.zip; dictionaries in entidades-regime-tributario-dicionarios/. CSV with header, ',' separator:
ano, cnpj (formatted 00.000.000/0000-00), cnpj_da_scp ('0' = the firm itself), forma_de_tributacao,
quantidade_de_escrituracoes. Mixed forms (e.g. 'LUCRO PRESUMIDO/REAL') appear in every file they cover; they are
de-duplicated here. Firms that do not file the ECF (Simples Nacional, MEI, inactive, public bodies) are absent.

Units: regime 'entidades-*.zip' -> parts/<stem>.parquet (rows whose CNPJ root is a debtor root);
       regime_rede 'regime.parquet' -> one row per (cnpj, ano, forma, scp, quantidade), files listed in `arquivos`.
"""
from __future__ import annotations

import hashlib
import json
import logging
import shutil
from pathlib import Path

from .collect import keys_glob
from .config import Settings
from .db import connect, q
from .enrich import _csv, _rejects, encoding_meta, extract_all
from .layouts import REGIME_COLS, REGIME_FILES
from .socios import _stale, keys_signature, manifest_sha
from .state import State, run_unit

log = logging.getLogger("cnpj_pipeline")


def _clean(c: str) -> str:
    return f"NULLIF(trim(regexp_replace({c}, '[\\r\\n\\t]', '', 'g')), '')"


def run(s: Settings, state: State, force: bool = False) -> Path:
    state.pin("key_buckets", s.key_buckets)
    if not list(s.collect_keys.glob("kb=*/keys.parquet")):
        raise SystemExit("regime: no debtor keys; run `collect` first")
    absent = [f for f in REGIME_FILES if not (s.rfb_dir / f).exists()]
    if absent:
        raise SystemExit(f"regime: {absent} not downloaded; run `download --regime` first")
    out, parts = s.regime_dir, s.regime_dir / "parts"
    extract_root = s.temp_dir / "extract" / s.ref_rfb
    ksig = keys_signature(state)
    con = connect(s)
    try:
        con.execute(f"CREATE OR REPLACE TEMP TABLE roots AS SELECT DISTINCT cnpj_raiz FROM read_parquet({keys_glob(s)}) "
                    "WHERE cnpj_raiz IS NOT NULL")
        for zname in REGIME_FILES:
            stem = zname.removesuffix(".zip")
            final = parts / f"{stem}.parquet"
            sha = manifest_sha(s, zname)
            stale = _stale(state, "regime", zname, sha256=sha, keys=ksig)
            if not (force or stale) and state.is_done("regime", zname) and final.exists():
                log.info("[regime] %s: done, skip", zname)
                continue
            csvs = extract_all(s.rfb_dir / zname, extract_root / stem)

            def work(tmp: Path, csvs=csvs, zname=zname, sha=sha) -> tuple[int, dict]:
                con.execute(f"""
                COPY (
                    WITH src AS (SELECT {_clean('ano')} AS ano, cnpj_norm({_clean('cnpj')}) AS cnpj,
                                        {_clean('cnpj_da_scp')} AS scp,
                                        upper({_clean('forma_de_tributacao')}) AS forma,
                                        {_clean('quantidade_de_escrituracoes')} AS qtd
                                 FROM {_csv(csvs, REGIME_COLS, header=True, delim=',')})
                    SELECT TRY_CAST(src.ano AS INTEGER) AS ano, src.cnpj, left(src.cnpj, 8) AS cnpj_raiz,
                           CASE WHEN src.scp IS NULL OR src.scp = '0' THEN NULL ELSE cnpj_norm(src.scp) END AS cnpj_da_scp,
                           src.forma AS forma_tributacao,
                           TRY_CAST(src.qtd AS INTEGER) AS quantidade_de_escrituracoes,
                           '{zname}' AS arquivo
                    FROM src JOIN roots r ON r.cnpj_raiz = left(src.cnpj, 8)
                ) TO {q(tmp)} (FORMAT PARQUET, COMPRESSION ZSTD)""")
                n = con.execute(f"SELECT COUNT(*) FROM read_parquet({q(tmp)})").fetchone()[0]
                return n, {"zip": zname, "sha256": sha, "keys": ksig, "members": [p.name for p in csvs],
                           "rejects": _rejects(con), "encoding": encoding_meta(csvs)}
            run_unit(state, "regime", zname, final, work, force=True)
            shutil.rmtree(extract_root / stem, ignore_errors=True)

        done = [(u, r, json.loads(m or "{}").get("sha256")) for u, st, r, m in state.units("regime") if st == "done"]
        psig = hashlib.md5(json.dumps(done + [ksig, s.key_buckets]).encode()).hexdigest()
        src = "read_parquet([" + ", ".join(q(parts / f"{f.removesuffix('.zip')}.parquet") for f in REGIME_FILES) + "])"

        def work(tmp: Path) -> tuple[int, dict]:
            con.execute(f"""
            COPY (
                WITH k AS (SELECT DISTINCT cnpj FROM read_parquet({keys_glob(s)}) WHERE cnpj IS NOT NULL)
                SELECT p.cnpj, p.cnpj_raiz, p.ano, p.forma_tributacao, p.cnpj_da_scp, p.quantidade_de_escrituracoes,
                       list_sort(list_distinct(list(p.arquivo))) AS arquivos,
                       bool_or(k.cnpj IS NOT NULL) AS cnpj_devedor,
                       kb(p.cnpj_raiz, {s.key_buckets}) AS kb, '{s.ref_rfb}' AS ref_rfb
                FROM {src} p LEFT JOIN k ON k.cnpj = p.cnpj
                WHERE p.cnpj IS NOT NULL
                GROUP BY p.cnpj, p.cnpj_raiz, p.ano, p.forma_tributacao, p.cnpj_da_scp, p.quantidade_de_escrituracoes
                ORDER BY p.cnpj, p.ano, p.forma_tributacao, p.cnpj_da_scp NULLS FIRST
            ) TO {q(tmp)} (FORMAT PARQUET, COMPRESSION ZSTD)""")
            n = con.execute(f"SELECT COUNT(*) FROM read_parquet({q(tmp)})").fetchone()[0]
            return n, {"parts": psig}
        run_unit(state, "regime_rede", "regime.parquet", out / "regime.parquet", work,
                 force=force or _stale(state, "regime_rede", "regime.parquet", parts=psig))
        _summary(con, out / "regime.parquet")
    finally:
        con.close()
    return out


def _summary(con, path: Path) -> None:
    t = f"read_parquet({q(path)})"
    n, roots = con.execute(f"SELECT COUNT(*), COUNT(DISTINCT cnpj_raiz) FROM {t}").fetchone()
    log.info("regime: %s rows, %s debtor roots with at least one ECF record", n, roots)
    for ano, forma, c in con.execute(f"SELECT ano, forma_tributacao, COUNT(DISTINCT cnpj_raiz) FROM {t} "
                                     "WHERE cnpj_da_scp IS NULL GROUP BY ALL ORDER BY 1, 2").fetchall():
        log.info("regime: ano=%s %s: %s debtor roots", ano, forma, c)


def signature(state: State) -> str:
    """For `build`: changes whenever regime.parquet is rebuilt."""
    r = state.get("regime_rede", "regime.parquet")
    return "none" if not r or r[0] != "done" else hashlib.md5(json.dumps([r[1], r[2]]).encode()).hexdigest()
