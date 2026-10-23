"""Stage `collect`: distinct debtor CNPJs from DEBITO (every tax, every snapshot), in yearly batches.

Unit 'year=YYYY' reads only the rows of that extraction year and writes Parquet partitioned by key bucket kb
(md5 of the 8-character root % key_buckets). Unit 'kb=K' then consolidates one bucket across years. Each unit is
atomic (state.run_unit), so a kill loses at most the unit in progress and never duplicates rows.

Source: the CEMEPI DuckDB file (attached READ_ONLY) or a Parquet glob of the DEBITO dump. Only the identifier, tax
type, extraction month and value are read: never NOME_DEVEDOR or CPF_DEVEDOR (CPF rows are excluded by the filter).
"""
from __future__ import annotations

import datetime as _dt
import json
import logging
import shutil
from pathlib import Path

from .config import Settings
from .db import connect, q
from .state import State, run_unit

log = logging.getLogger("cnpj_pipeline")

TRIBUTO_SQL = """CASE WHEN TIPO_DEBITO ILIKE '%ICMS%'  THEN 'ICMS'
                      WHEN TIPO_DEBITO ILIKE '%IPVA%'  THEN 'IPVA'
                      WHEN TIPO_DEBITO ILIKE '%ITCMD%' THEN 'ITCMD'
                      ELSE 'OUTROS' END"""
# Same legal-entity filter as the EDA (cell 22): a CNPJ and no CPF.
YEAR_SQL = "TRY_CAST(ANO_EXTRACAO AS INTEGER)"
MONTH_SQL = "TRY_CAST(MES_EXTRACAO AS INTEGER)"
# Valid extraction snapshot: plausible year and month 1-12. The source has garbage years (2, 4, 20242024, ...).
MIN_YEAR = 2000
PJ_FILTER = "COALESCE(CAST(CPF_DEVEDOR AS VARCHAR), '') = '' AND COALESCE(CAST(CNPJ_DEVEDOR AS VARCHAR), '') <> ''"


def source_relation(duckdb_path: Path | None, debito_parquet: str | None, table: str) -> tuple[str, Path | None]:
    if debito_parquet:
        return f"read_parquet({q(debito_parquet)}, union_by_name = true)", None
    if duckdb_path:
        return f'src."{table}"', Path(duckdb_path)
    raise SystemExit("collect needs --duckdb PATH or --debito-parquet GLOB")


def run(s: Settings, state: State, duckdb_path: Path | None, debito_parquet: str | None,
        table: str = "DEBITO", years: list[int] | None = None, force: bool = False) -> None:
    state.pin("key_buckets", s.key_buckets)
    state.pin("ref_rfb", s.ref_rfb)
    rel, attach = source_relation(duckdb_path, debito_parquet, table)
    ref_y, ref_m = map(int, s.ref_rfb.split("-"))
    con = connect(s, attach)
    try:
        max_year = _dt.date.today().year
        counts = con.execute(f"SELECT {YEAR_SQL} AS y, COUNT(*) FROM {rel} GROUP BY 1 ORDER BY 1").fetchall()
        valid = [y for y, _ in counts if y is not None and MIN_YEAR <= y <= max_year]
        junk = [(y, n) for y, n in counts if y not in valid]
        log.info("collect: valid extraction years %s", valid)
        if junk:
            log.warning("collect: ignoring %d rows with invalid ANO_EXTRACAO (value: rows) %s",
                        sum(n for _, n in junk), ", ".join(f"{y}: {n}" for y, n in junk))
        todo = [y for y in valid if not years or y in years]
        # Drop units/outputs from invalid years left by earlier versions (they would leak into consolidation).
        for u, *_ in state.units("collect"):
            if u.startswith("year=") and int(u[5:]) not in valid:
                state.invalidate("collect", u)
                shutil.rmtree(s.collect_raw / u, ignore_errors=True)
                log.info("collect: removed stale unit %s", u)
        log.info("collect: processing %s", todo)

        for y in todo:
            def work(tmp: Path, y=y) -> tuple[int, dict]:
                con.execute(f"""
                COPY (
                    SELECT cnpj, kb(left(cnpj, 8), {s.key_buckets}) AS kb, tributo,
                           MIN(snap) AS first_snap, MAX(snap) AS last_snap,
                           COUNT(DISTINCT snap) AS n_snapshots,
                           COUNT(*) FILTER (WHERE snap = {ref_y * 100 + ref_m}) AS n_cda_ref,
                           SUM(valor) FILTER (WHERE snap = {ref_y * 100 + ref_m}) AS valor_ref
                    FROM (SELECT cnpj_norm(CNPJ_DEVEDOR) AS cnpj, {TRIBUTO_SQL} AS tributo,
                                 {YEAR_SQL} * 100 + {MONTH_SQL} AS snap,
                                 TRY_CAST(VALOR_SEM_HONORARIOS AS DOUBLE) AS valor
                          FROM {rel}
                          WHERE {YEAR_SQL} = {y} AND {MONTH_SQL} BETWEEN 1 AND 12 AND {PJ_FILTER})
                    WHERE cnpj IS NOT NULL
                    GROUP BY ALL
                ) TO {q(tmp)} (FORMAT PARQUET, COMPRESSION ZSTD, PARTITION_BY (kb))""")
                if not any(tmp.glob("**/*.parquet")):      # no legal-entity rows that year
                    tmp.mkdir(parents=True, exist_ok=True)
                    return 0, {"year": y}
                n = con.execute(f"SELECT COUNT(*) FROM read_parquet({q(tmp / '**' / '*.parquet')})").fetchone()[0]
                return n, {"year": y}
            run_unit(state, "collect", f"year={y}", s.collect_raw / f"year={y}", work, force=force)

        # Consolidation by bucket. Re-run whenever a year was (re)collected: keys depend on all years.
        years_done = sorted(u for u, st, *_ in state.units("collect") if st == "done" and u.startswith("year="))
        sig = ",".join(years_done)
        for k in range(s.key_buckets):
            unit = f"kb={k:03d}"
            prev = state.get("collect_keys", unit)
            stale = prev is not None and json.loads(prev[2] or "{}").get("years") != sig
            src = q(s.collect_raw / "year=*" / f"kb={k}" / "*.parquet")
            has = con.execute(f"SELECT COUNT(*) FROM glob({src})").fetchone()[0] > 0

            def keys_work(tmp: Path, src=src, has=has) -> tuple[int, dict]:
                tmp.mkdir()
                if not has:
                    con.execute(f"COPY (SELECT NULL::VARCHAR AS cnpj LIMIT 0) TO {q(tmp / 'empty.parquet')} (FORMAT PARQUET)")
                    return 0, {"years": sig}
                con.execute(f"""
                COPY (
                    SELECT cnpj, left(cnpj, 8) AS cnpj_raiz, cnpj_dv_ok(cnpj) AS dv_valido,
                           regexp_full_match(cnpj, '.*[A-Z].*') AS cnpj_alfanumerico,
                           list_sort(list_distinct(list(tributo))) AS tributos,
                           MIN(first_snap) AS first_snap, MAX(last_snap) AS last_snap,
                           SUM(n_cda_ref) AS n_cda_ref, SUM(valor_ref) AS valor_ref
                    FROM read_parquet({src}) GROUP BY cnpj ORDER BY cnpj
                ) TO {q(tmp / 'keys.parquet')} (FORMAT PARQUET, COMPRESSION ZSTD)""")
                con.execute(f"""
                COPY (SELECT cnpj, tributo, MIN(first_snap) AS first_snap, MAX(last_snap) AS last_snap,
                             SUM(n_cda_ref) AS n_cda_ref, SUM(valor_ref) AS valor_ref
                      FROM read_parquet({src}) GROUP BY ALL ORDER BY cnpj, tributo
                ) TO {q(tmp / 'keys_tributo.parquet')} (FORMAT PARQUET, COMPRESSION ZSTD)""")
                n = con.execute(f"SELECT COUNT(*) FROM read_parquet({q(tmp / 'keys.parquet')})").fetchone()[0]
                return n, {"years": sig}
            run_unit(state, "collect_keys", unit, s.collect_keys / unit, keys_work, force=force or stale)
        summary(s, con)
    finally:
        con.close()


def keys_glob(s: Settings, name: str = "keys.parquet") -> str:
    return q(s.collect_keys / "kb=*" / name)


def summary(s: Settings, con) -> None:
    r = con.execute(f"""SELECT COUNT(*), COUNT(DISTINCT cnpj_raiz), AVG(dv_valido::INT) * 100,
                               SUM(cnpj_alfanumerico::INT)
                        FROM read_parquet({keys_glob(s)}) WHERE cnpj IS NOT NULL""").fetchone()
    log.info("collect: %s distinct CNPJs, %s roots, %.2f%% valid check digits, %s alphanumeric",
             r[0], r[1], r[2] or 0, r[3])
    if r[0] and (r[2] or 0) < 90:
        log.warning("collect: under 90%% of the CNPJs have valid check digits; the identifier may be masked. "
                    "Check before enriching.")
