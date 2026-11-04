"""Load the December 2025 declared and assessed CDAs and run the diagnostic.

The extract stays on the external disk. This module lists every column it
reads. It does not select NOME_DEVEDOR or CPF_DEVEDOR. Run it on the machine
that has the disk; the tests use synthetic rows and do not call the loader.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pandas as pd
from dotenv import dotenv_values

HERE = Path(__file__).resolve().parent


def _load_diagnose():
    spec = importlib.util.spec_from_file_location("lin_diagnose", HERE / "diagnose.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


diagnose = _load_diagnose()
MEMORY_LIMIT = "900MB"
AS_OF = "2025-12-31"
PAY_END = "2026-01-01"
SAMPLE_CAP = diagnose.SAMPLE_CAP
# Section 3.15, lab/eda/2026-10-31_cnae_declared_assessed/tables/segment_summary.csv.
BASELINE_N = {"declared": 2_257_189, "assessed": 59_542}
BASELINE_STOCK = {
    "declared": 116_424_184_243.9787,
    "assessed": 311_398_391_642.14594,
}
ICMS_COLUMNS = (
    "ID_DEBITO",
    "TIPO_DEBITO",
    "ICMS_ORIGIN",
    "VALOR_SEM_HONORARIOS",
    "DATA_INSCRICAO",
    "CNPJ",
    "CNPJ_ROOT",
)
GARE_COLUMNS = ("ID_DEBITO", "VALOR_RECEITA", "DATA_ARRECADACAO_GARE")
FIRM_COLUMNS = (
    "cnpj",
    "match_nivel",
    "cnae_principal",
    "porte",
    "situacao_cadastral",
    "uf",
    "municipio_nome",
)
PERSON_COLUMNS = ("NOME_DEVEDOR", "CPF_DEVEDOR")
TABLES = (
    "coefficients",
    "reset",
    "bins",
    "spline",
    "categories",
    "porte_shape",
    "groups",
    "icc",
    "quantiles",
    "decision",
)


def assert_safe_sql(sql: str) -> None:
    folded = sql.upper()
    for name in PERSON_COLUMNS:
        if name in folded:
            raise RuntimeError(
                f"The query selects {name}. The lab does not read that column."
            )
    if "SELECT *" in folded:
        raise RuntimeError("The query must list columns.")


def sql_texts(icms: str, gare_files: list[str], firm: str) -> dict[str, str]:
    """Every statement the loader executes, with paths filled in."""
    gare_list = ", ".join("'" + path.replace("'", "") + "'" for path in gare_files)
    icms_cols = ", ".join(ICMS_COLUMNS)
    gare_cols = ", ".join(GARE_COLUMNS)
    firm_cols = ", ".join(FIRM_COLUMNS)
    texts = {
        "settings": f"SET memory_limit='{MEMORY_LIMIT}'",
        "snapshot": f"""
            SELECT {icms_cols}
            FROM read_parquet('{icms}')
            WHERE MES_EXTRACAO = 12
              AND ICMS_ORIGIN IN ('declared', 'assessed')
        """,
        "payments": f"""
            SELECT ID_DEBITO, sum(VALOR_RECEITA) AS paid
            FROM (
                SELECT {gare_cols}
                FROM read_parquet([{gare_list}])
                WHERE DATA_ARRECADACAO_GARE < DATE '{PAY_END}'
            )
            GROUP BY 1
        """,
        "firms": f"""
            SELECT {firm_cols}
            FROM read_parquet('{firm}')
        """,
        "sample": """
            SELECT segment, stock, paid, debt_age, porte, section, division,
                   situacao_cadastral, municipality, uf, root, paid_flag
            FROM ranked
            JOIN alloc USING (segment, porte, paid_flag)
            WHERE rk <= n_take
        """,
    }
    for text in texts.values():
        assert_safe_sql(text)
    return texts


def _repo_root() -> Path:
    for path in HERE.parents:
        if (path / "pyproject.toml").exists() and (path / "lab").exists():
            return path
    raise SystemExit("Repository root not found.")


def source_paths() -> dict[str, Path]:
    env = dotenv_values(_repo_root() / ".env")
    missing = [
        key
        for key in ("CEMEPI_INTERIM", "CNPJ_PIPELINE_WORKDIR", "CEMEPI_DATA_ROOT")
        if not env.get(key)
    ]
    if missing:
        raise SystemExit(
            f"Missing {', '.join(missing)} in .env. The extract is read on the local disk."
        )
    interim = Path(env["CEMEPI_INTERIM"])
    work = Path(env["CNPJ_PIPELINE_WORKDIR"])
    out = {
        "icms": interim / "icms" / "year=2025",
        "gare": interim / "gare_icms",
        "firm": work / "build" / "2026-03" / "cnpj_enriquecido",
        "tmp": work / "tmp",
    }
    if not out["icms"].exists() or not out["gare"].exists() or not out["firm"].exists():
        raise SystemExit(
            "The ICMS extract, the GARE extract or the CNPJ table is not available."
        )
    out["tmp"].mkdir(parents=True, exist_ok=True)
    return out


def _hierarchy() -> pd.DataFrame:
    path = HERE.parent / "2026-10-31_cnae_declared_assessed" / "analyze.py"
    spec = importlib.util.spec_from_file_location("cnae_declared_assessed", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    xlsx = path.parent / "reference" / "CNAE_Subclasses_2_3_Estrutura_Detalhada.xlsx"
    hier = module.parse_cnae23(xlsx)
    return hier.loc[:, ["subclass", "section", "division"]].drop_duplicates("subclass")


def _connect():
    import duckdb

    con = duckdb.connect()
    tmp = Path("/tmp/cemepi_lin_duckdb")
    tmp.mkdir(parents=True, exist_ok=True)
    con.execute(
        f"""
        SET memory_limit='{MEMORY_LIMIT}';
        SET threads=2;
        SET temp_directory='{tmp}';
        SET preserve_insertion_order=false
        """
    )
    return con


def _check_baseline(counts: pd.DataFrame) -> None:
    for segment, expected_n in BASELINE_N.items():
        row = counts.loc[counts["segment"] == segment]
        if row.empty:
            raise SystemExit(
                f"Segment {segment} is absent. Section 3.15 has {expected_n} CDAs."
            )
        got_n = int(row["n_cda"].iloc[0])
        got_stock = float(row["stock"].iloc[0])
        if got_n != expected_n:
            raise SystemExit(
                f"Segment {segment} has {got_n} CDAs. "
                f"Section 3.15 segment_summary.csv has {expected_n}."
            )
        expected_stock = BASELINE_STOCK[segment]
        if abs(got_stock - expected_stock) / expected_stock > 1e-6:
            raise SystemExit(
                f"Segment {segment} stock is {got_stock}. "
                f"Section 3.15 segment_summary.csv has {expected_stock}."
            )


def load_estimation_frame() -> tuple[pd.DataFrame, dict]:
    """Stratified sample of at most 200,000 complete CDAs. Population counts are checked first."""
    src = source_paths()
    icms = sorted((src["icms"]).glob("*.parquet"))
    if not icms:
        raise SystemExit("No December parquet files in the ICMS extract.")
    gare_files = [
        str(path)
        for year in range(2016, 2026)
        for path in sorted((src["gare"] / f"year={year}").glob("*.parquet"))
    ]
    if not gare_files:
        raise SystemExit("No GARE files for 2016–2025.")
    firm = sorted(src["firm"].glob("*.parquet"))
    if not firm:
        raise SystemExit("The CNPJ table has no parquet files.")
    texts = sql_texts(str(icms[0]), gare_files, str(firm[0]))
    icms_glob = str(src["icms"] / "*.parquet")
    firm_glob = str(src["firm"] / "*.parquet")
    texts["snapshot"] = sql_texts(icms_glob, gare_files, firm_glob)["snapshot"]
    texts["firms"] = sql_texts(icms_glob, gare_files, firm_glob)["firms"]
    con = _connect()
    hier = _hierarchy()
    con.register("hier_df", hier)
    con.execute("CREATE TABLE hier AS SELECT subclass, section, division FROM hier_df")
    con.execute(
        f"""
        CREATE TABLE cda AS
        SELECT ID_DEBITO, ICMS_ORIGIN AS segment, VALOR_SEM_HONORARIOS AS stock,
               DATA_INSCRICAO AS enrol,
               CASE
                 WHEN DATA_INSCRICAO BETWEEN DATE '1960-01-01' AND DATE '{AS_OF}'
                 THEN date_diff('day', DATA_INSCRICAO, DATE '{AS_OF}') / 365.25
               END AS debt_age,
               CNPJ AS cnpj, CNPJ_ROOT AS root
        FROM ({texts["snapshot"]})
        """
    )
    con.execute(f"CREATE TABLE pay AS {texts['payments']}")
    con.execute(
        f"""
        CREATE TABLE firm AS
        SELECT cnpj, match_nivel, cnae_principal, porte, situacao_cadastral, uf, municipio_nome
        FROM ({texts["firms"]})
        """
    )
    con.execute(
        """
        CREATE TABLE base AS
        SELECT c.segment, c.stock, c.debt_age, c.root,
               coalesce(p.paid, 0) AS paid,
               CASE WHEN f.match_nivel = 'estabelecimento' THEN f.porte END AS porte,
               CASE WHEN f.match_nivel = 'estabelecimento' THEN f.situacao_cadastral END
                 AS situacao_cadastral,
               CASE WHEN f.match_nivel = 'estabelecimento' THEN f.uf END AS uf,
               CASE WHEN f.match_nivel = 'estabelecimento' THEN f.municipio_nome END
                 AS municipio_nome,
               h.section, h.division
        FROM cda c
        LEFT JOIN pay p USING (ID_DEBITO)
        LEFT JOIN firm f ON f.cnpj = c.cnpj
        LEFT JOIN hier h
          ON h.subclass = CASE
               WHEN f.match_nivel = 'estabelecimento' THEN f.cnae_principal
             END
        """
    )
    counts = con.execute(
        """
        SELECT segment, count(*) AS n_cda, sum(stock) AS stock
        FROM base
        GROUP BY 1
        ORDER BY 1
        """
    ).df()
    _check_baseline(counts)
    population = {
        "n_declared": int(counts.loc[counts.segment == "declared", "n_cda"].iloc[0]),
        "n_assessed": int(counts.loc[counts.segment == "assessed", "n_cda"].iloc[0]),
        "stock_declared": float(
            counts.loc[counts.segment == "declared", "stock"].iloc[0]
        ),
        "stock_assessed": float(
            counts.loc[counts.segment == "assessed", "stock"].iloc[0]
        ),
    }
    strata = con.execute(
        """
        SELECT segment,
               coalesce(porte, 'unknown') AS porte,
               CASE WHEN paid > 0 THEN 1 ELSE 0 END AS paid_flag,
               count(*) AS n
        FROM base
        WHERE stock > 0 AND debt_age IS NOT NULL
        GROUP BY 1, 2, 3
        """
    ).df()
    n_before = int(strata["n"].sum())
    allocation = (
        diagnose.allocate_strata(
            strata.set_index(["segment", "porte", "paid_flag"])["n"],
            SAMPLE_CAP,
        )
        .rename("n_take")
        .reset_index()
    )
    con.register("alloc_df", allocation)
    con.execute("CREATE TABLE alloc AS SELECT * FROM alloc_df")
    sample = con.execute(
        f"""
        WITH eligible AS (
          SELECT segment, stock, paid, debt_age,
                 coalesce(porte, 'unknown') AS porte,
                 coalesce(section, 'unmatched') AS section,
                 coalesce(division, 'unmatched') AS division,
                 situacao_cadastral,
                 coalesce(municipio_nome, 'Unknown') AS municipality,
                 coalesce(uf, 'NA') AS uf,
                 root,
                 CASE WHEN paid > 0 THEN 1 ELSE 0 END AS paid_flag,
                 row_number() OVER (
                   PARTITION BY segment, coalesce(porte, 'unknown'),
                                CASE WHEN paid > 0 THEN 1 ELSE 0 END
                   ORDER BY hash(root || CAST(stock AS VARCHAR))
                 ) AS rk
          FROM base
          WHERE stock > 0 AND debt_age IS NOT NULL
        )
        {texts["sample"].replace("FROM ranked", "FROM eligible")}
        """
    ).df()
    con.close()
    population["n_eligible"] = n_before
    population["n_sample"] = len(sample)
    print(
        f"population CDAs: declared {population['n_declared']}, assessed {population['n_assessed']}",
        flush=True,
    )
    print(f"eligible CDAs before the sample: {n_before}", flush=True)
    print(f"estimation sample drawn: {population['n_sample']}", flush=True)
    diagnose.reject_person_columns(sample)
    return sample, population


def write_tables(result: dict, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for key in TABLES:
        frame = result.get(key)
        if isinstance(frame, pd.DataFrame):
            frame.to_csv(out_dir / f"{key}.csv", index=False)


def run(out_dir: Path | None = None) -> dict:
    sample, population = load_estimation_frame()
    result = diagnose.run_diagnostics(sample)
    result["population"] = population
    if out_dir is not None:
        write_tables(result, out_dir)
        pd.DataFrame([population]).to_csv(out_dir / "population.csv", index=False)
    return result


if __name__ == "__main__":
    run(HERE / "tables")
