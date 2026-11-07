"""Origin models for declared and assessed VAT (ICMS) certificates.

The certificate is the unit. The firm key is the eight-digit root and is used
only to cluster or to demean. It is never written. NOME_DEVEDOR and
CPF_DEVEDOR are never read. Counts from 1 to 4 are left blank.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import statsmodels.api as sm
import statsmodels.formula.api as smf
from statsmodels.tools.sm_exceptions import PerfectSeparationError

CACHE_VERSION = "avd-origin-1"
PAY_START = "2016-01-01"
PAY_END = "2026-01-01"
AS_OF = "2025-12-31"
ENROL_MIN = "1960-01-01"
OPEN_MIN = "1900-01-01"
Z_95 = 1.959963984540054
MIN_FIRMS = 5
SECTION_REFERENCE = "G"
SIZE_REFERENCE = "01"
FORBIDDEN_COLUMNS = ("NOME_DEVEDOR", "CPF_DEVEDOR")
FRAME_COLUMNS = (
    "origin",
    "stock",
    "paid",
    "paid_flag",
    "debt_age",
    "cohort",
    "section",
    "porte",
    "firm_age",
    "firm_age_missing",
    "root",
)
BANNED_OUTPUT = {
    "root",
    "cnpj",
    "cnpj_devedor",
    "nome_devedor",
    "cpf_devedor",
    "id_debito",
    "firm_key",
}
CONTROLS_NOTE = (
    "Controls: log stock, debt age, firm age, a missing-opening indicator, "
    "enrolment year, CNAE section and firm size. "
    "Declared origin is the reference. "
    "The main interval is clustered by the eight-digit firm key. "
    "HC3 is the robustness column. "
    "A count from 1 to 4 is left blank. The firm key is not printed."
)
RECOVERY_NOTE = (
    "Percentage difference in expected payments per real of stock, "
    "from a log-link model with the stock as the exposure. "
    "Controls: debt age, firm age, a missing-opening indicator, "
    "enrolment year, CNAE section and firm size. "
    "Declared origin is the reference. "
    "The main interval is clustered by the eight-digit firm key. "
    "HC3 is the robustness column. "
    "A count from 1 to 4 is left blank. The firm key is not printed."
)
WITHIN_NOTE = (
    "Linear probability model with the firm mean removed. "
    "The sample is firms with at least one declared certificate and "
    "at least one assessed certificate. "
    "Certificate-level regressors that vary inside the firm stay in the model. "
    "The main interval is clustered by the firm. "
    "HC3 is the robustness column of the within regression. "
    "A count from 1 to 4 is left blank. The firm key is not printed."
)

_FRAME: pd.DataFrame | None = None


def find_repo(start: Path | None = None) -> Path:
    origin = start or Path.cwd()
    for path in [origin, *origin.parents]:
        if (path / "AGENTS.md").exists() and (path / "pyproject.toml").exists():
            return path
    raise RuntimeError("repository root not found")


def manuscript_dirs(repo: Path | None = None) -> tuple[Path, Path]:
    root = repo or find_repo()
    base = root / "projects" / "macro" / "manuscript" / "icms_declarado_autuado"
    figures = base / "figures"
    tables = base / "tables"
    figures.mkdir(parents=True, exist_ok=True)
    tables.mkdir(parents=True, exist_ok=True)
    return figures, tables


def assert_safe_sql(sql: str) -> None:
    folded = " ".join(sql.upper().split())
    for name in FORBIDDEN_COLUMNS:
        if name in folded:
            raise RuntimeError(f"The query selects {name}.")
    if "SELECT *" in folded:
        raise RuntimeError("The query must list columns.")


def assert_public_frame(frame: pd.DataFrame) -> None:
    found = BANNED_OUTPUT & {str(column).lower() for column in frame.columns}
    if found:
        raise RuntimeError(f"Refusing to write identifier columns: {sorted(found)}")


def cache_dir() -> Path:
    base = os.environ.get("CEMEPI_CACHE")
    if base:
        path = Path(base) / "results" / "avd-origin"
    else:
        path = Path(os.environ.get("TMPDIR", tempfile.gettempdir())) / "avd-origin"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _signature(frame: pd.DataFrame, label: str, formula: str) -> str:
    digest = hashlib.sha256()
    digest.update(CACHE_VERSION.encode())
    digest.update(label.encode())
    digest.update(formula.encode())
    digest.update(str(len(frame)).encode())
    digest.update(str(int(frame["paid_flag"].sum())).encode())
    digest.update(f"{float(frame['stock'].sum()):.4f}".encode())
    digest.update(f"{float(frame['paid'].sum()):.4f}".encode())
    assessed = int((frame["origin"] == "assessed").sum())
    digest.update(str(assessed).encode())
    return digest.hexdigest()[:32]


def _read_cached(key: str) -> pd.DataFrame | None:
    path = cache_dir() / f"{key}.parquet"
    marker = cache_dir() / f"{key}.ok"
    if not path.exists() or not marker.exists():
        return None
    frame = pd.read_parquet(path)
    assert_public_frame(frame)
    return frame


def _write_cached(key: str, frame: pd.DataFrame) -> None:
    assert_public_frame(frame)
    path = cache_dir() / f"{key}.parquet"
    frame.to_parquet(path, index=False)
    (cache_dir() / f"{key}.ok").write_text("table\n", encoding="utf-8")


def blank_count(value: float | None) -> str:
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return ""
    number = int(value)
    if 1 <= number <= 4:
        return ""
    return f"{number:,}"


def format_number(value: float | None, digits: int = 2) -> str:
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return ""
    return f"{float(value):,.{digits}f}"


def percent_difference(beta: float, se: float) -> tuple[float, float, float]:
    """Percentage difference and the transformed 95 percent interval."""
    point = 100.0 * (np.exp(beta) - 1.0)
    low = 100.0 * (np.exp(beta - Z_95 * se) - 1.0)
    high = 100.0 * (np.exp(beta + Z_95 * se) - 1.0)
    return float(point), float(low), float(high)


def _tex_escape(value: object) -> str:
    return (
        str(value)
        .replace("\\", r"\textbackslash{}")
        .replace("_", r"\_")
        .replace("%", r"\%")
        .replace("&", r"\&")
        .replace("$", r"\$")
    )


def write_table(
    frame: pd.DataFrame,
    path: Path,
    caption: str,
    label: str,
    notes: str,
) -> None:
    assert_public_frame(frame)
    align = "l" + "r" * (frame.shape[1] - 1)
    header = " & ".join(_tex_escape(column) for column in frame.columns)
    body_rows = []
    for row in frame.itertuples(index=False):
        body_rows.append(" & ".join(_tex_escape(value) for value in row) + r" \\")
    body = "\n".join(body_rows)
    tex = (
        "% generated by the supplementary notebook; do not edit by hand\n"
        "\\begin{table}[t]\n\\centering\n"
        f"\\caption{{{caption}}}\\label{{{label}}}\n"
        f"\\begin{{tabular}}{{{align}}}\n"
        "\\toprule\n"
        f"{header} \\\\\n"
        "\\midrule\n"
        f"{body}\n"
        "\\bottomrule\n"
        "\\end{tabular}\n\n"
        "\\vspace{0.4em}\n"
        "\\begin{minipage}{0.95\\textwidth}\n"
        "\\footnotesize\n"
        f"{notes}\n"
        "\\end{minipage}\n"
        "\\end{table}\n"
    )
    path.write_text(tex, encoding="utf-8")
    frame.to_csv(path.with_suffix(".csv"), index=False)


def _database_path() -> Path:
    if os.environ.get("CEMEPI_DUCKDB"):
        return Path(os.environ["CEMEPI_DUCKDB"])
    if os.environ.get("CEMEPI_DATA_ROOT"):
        return Path(os.environ["CEMEPI_DATA_ROOT"]) / "api_ctf" / "cemepi.duckdb"
    raise RuntimeError(
        "Set CEMEPI_DUCKDB or CEMEPI_DATA_ROOT. The extract is read on the local disk."
    )


def _enrichment_dir(database: Path) -> Path:
    if os.environ.get("CNPJ_PIPELINE_WORKDIR"):
        work = Path(os.environ["CNPJ_PIPELINE_WORKDIR"])
    else:
        work = database.parent.parent / "cnpj_enrich"
    path = work / "build" / "2026-03" / "cnpj_enriquecido"
    if not path.exists():
        raise RuntimeError(
            "The 2026-03 firm register was not found. "
            "Set CNPJ_PIPELINE_WORKDIR to the register build."
        )
    return path


def _connect():
    import duckdb

    database = _database_path()
    if not database.exists():
        raise RuntimeError(f"Database file not found: {database.name}")
    scratch = Path(tempfile.mkdtemp(prefix="avd_origin_"))
    connection = duckdb.connect(
        config={
            "memory_limit": "2500MB",
            "threads": 4,
            "temp_directory": str(scratch),
            "preserve_insertion_order": False,
        }
    )
    connection.execute(f"ATTACH '{database}' AS src (READ_ONLY)")
    return connection


def firm_sql(enrichment_glob: str) -> str:
    sql = f"""
        CREATE OR REPLACE TEMP TABLE avd_firm AS
        SELECT cnpj, match_nivel, cnae_secao, porte, data_inicio_atividade
        FROM read_parquet('{enrichment_glob}')
    """
    assert_safe_sql(sql)
    return sql


def _snapshot_sql() -> str:
    """Certificate rows for the December 2025 legal-entity comparison, one row per certificate."""
    sql = f"""
        WITH raw AS (
            SELECT
                ID_DEBITO,
                CASE
                    WHEN TIPO_DEBITO = 'ICMS Declarado' THEN 'declared'
                    WHEN TIPO_DEBITO LIKE 'ICMS Autua%' THEN 'assessed'
                END AS origin,
                VALOR_SEM_HONORARIOS AS stock,
                CASE
                    WHEN TRY_CAST(LEFT(DATA_INSCRICAO, 10) AS DATE)
                         BETWEEN DATE '{ENROL_MIN}' AND DATE '{AS_OF}'
                    THEN date_diff(
                        'day',
                        TRY_CAST(LEFT(DATA_INSCRICAO, 10) AS DATE),
                        DATE '{AS_OF}'
                    ) / 365.25
                END AS debt_age,
                CASE
                    WHEN TRY_CAST(LEFT(DATA_INSCRICAO, 10) AS DATE)
                         BETWEEN DATE '{ENROL_MIN}' AND DATE '{AS_OF}'
                    THEN CAST(year(TRY_CAST(LEFT(DATA_INSCRICAO, 10) AS DATE)) AS VARCHAR)
                    ELSE 'unknown'
                END AS cohort,
                nullif(
                    left(regexp_replace(coalesce(CNPJ_DEVEDOR, ''), '[^0-9]', '', 'g'), 8),
                    ''
                ) AS root,
                CASE
                    WHEN regexp_full_match(
                        regexp_replace(upper(CAST(CNPJ_DEVEDOR AS VARCHAR)), '[^0-9A-Z]', '', 'g'),
                        '[0-9]{{12,13}}'
                    )
                    THEN lpad(
                        regexp_replace(upper(CAST(CNPJ_DEVEDOR AS VARCHAR)), '[^0-9A-Z]', '', 'g'),
                        14, '0'
                    )
                    ELSE nullif(
                        regexp_replace(
                            upper(CAST(CNPJ_DEVEDOR AS VARCHAR)), '[^0-9A-Z]', '', 'g'
                        ),
                        ''
                    )
                END AS cnpj
            FROM src.DEBITO
            WHERE ANO_EXTRACAO = 2025
              AND MES_EXTRACAO = 12
              AND TIPO_DEBITO IN (
                  'ICMS Declarado', 'ICMS Autuação', 'ICMS Autuação Simples Nacional'
              )
              AND VALOR_SEM_HONORARIOS > 0
        ),
        one AS (
            SELECT origin, stock, debt_age, cohort, root, cnpj, ID_DEBITO
            FROM raw
            WHERE length(cnpj) = 14
            QUALIFY row_number() OVER (
                PARTITION BY ID_DEBITO ORDER BY stock DESC
            ) = 1
        ),
        pay AS (
            SELECT ID_DEBITO, sum(VALOR_RECEITA) AS paid
            FROM src.ARRECADACAO
            WHERE DATA_ARRECADACAO_GARE >= TIMESTAMP '{PAY_START}'
              AND DATA_ARRECADACAO_GARE < TIMESTAMP '{PAY_END}'
            GROUP BY ID_DEBITO
        )
        SELECT
            one.origin,
            one.stock,
            coalesce(pay.paid, 0) AS paid,
            CASE WHEN coalesce(pay.paid, 0) > 0 THEN 1 ELSE 0 END AS paid_flag,
            one.debt_age,
            one.cohort,
            CASE
                WHEN firm.match_nivel = 'estabelecimento' AND firm.cnae_secao IS NOT NULL
                THEN firm.cnae_secao ELSE 'unknown'
            END AS section,
            CASE
                WHEN firm.match_nivel = 'estabelecimento'
                     AND firm.porte IS NOT NULL AND firm.porte <> ''
                THEN firm.porte ELSE 'unknown'
            END AS porte,
            CASE
                WHEN firm.match_nivel = 'estabelecimento'
                     AND firm.data_inicio_atividade BETWEEN DATE '{OPEN_MIN}' AND DATE '{AS_OF}'
                THEN date_diff('day', firm.data_inicio_atividade, DATE '{AS_OF}') / 365.25
                ELSE 0
            END AS firm_age,
            CASE
                WHEN firm.match_nivel = 'estabelecimento'
                     AND firm.data_inicio_atividade BETWEEN DATE '{OPEN_MIN}' AND DATE '{AS_OF}'
                THEN 0 ELSE 1
            END AS firm_age_missing,
            one.root
        FROM one
        LEFT JOIN pay USING (ID_DEBITO)
        LEFT JOIN avd_firm AS firm ON firm.cnpj = one.cnpj
    """
    assert_safe_sql(sql)
    return sql


def usable_connection(candidate):
    """Reuse an open connection. A closed one is ignored."""
    if candidate is None or not callable(getattr(candidate, "execute", None)):
        return None
    try:
        import duckdb

        errors: tuple[type[BaseException], ...] = (
            OSError,
            RuntimeError,
            ValueError,
            AttributeError,
            duckdb.Error,
        )
    except ImportError:
        errors = (OSError, RuntimeError, ValueError, AttributeError)
    try:
        candidate.execute("SELECT 1")
    except errors:
        return None
    return candidate


def load_analysis_frame(connection=None) -> pd.DataFrame:
    """Load the comparison population. The frame stays in memory."""
    global _FRAME
    if _FRAME is not None:
        return _FRAME
    owns = connection is None
    con = connection if connection is not None else _connect()
    try:
        database = _database_path()
        enrichment = _enrichment_dir(database)
        glob = str(enrichment / "*.parquet").replace("'", "")
        con.execute(firm_sql(glob))
        frame = con.execute(_snapshot_sql()).df()
    finally:
        if owns:
            con.close()
    missing = [column for column in FRAME_COLUMNS if column not in frame.columns]
    if missing:
        raise RuntimeError(f"The analysis frame is missing {missing}.")
    extra = [column for column in frame.columns if column not in FRAME_COLUMNS]
    if extra:
        raise RuntimeError(f"The analysis frame has unexpected columns {extra}.")
    _FRAME = frame
    return _FRAME


def clear_frame() -> None:
    global _FRAME
    _FRAME = None


def _pool_rare(
    levels: pd.Series, firms: pd.Series, min_firms: int = MIN_FIRMS
) -> pd.Series:
    table = pd.DataFrame({"level": levels.astype(str), "firm": firms})
    counts = table.groupby("level")["firm"].nunique()
    rare = set(counts[counts < min_firms].index)
    if not rare:
        return levels.astype(str)
    return levels.astype(str).where(~levels.astype(str).isin(rare), other="pooled")


def _reference(levels: pd.Series, preferred: str) -> str:
    present = set(levels.astype(str))
    if preferred in present:
        return preferred
    mode = levels.astype(str).mode(dropna=True)
    if mode.empty:
        raise RuntimeError("A categorical control has no level.")
    return str(mode.iloc[0])


def prepare_population(frame: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, int]]:
    """Drop rows that cannot enter the clustered logit, and pool sparse categories."""
    work = frame.loc[:, list(FRAME_COLUMNS)].copy()
    account = {
        "n_input": len(work),
        "n_dropped_root": int(work["root"].isna().sum() + (work["root"] == "").sum()),
        "n_dropped_age": int(work["debt_age"].isna().sum()),
    }
    work = work.loc[work["root"].notna() & (work["root"] != "")].copy()
    work = work.loc[work["debt_age"].notna()].copy()
    work["assessed"] = (work["origin"] == "assessed").astype(int)
    work["log_stock"] = np.log1p(work["stock"].astype(float))
    work["paid"] = work["paid"].astype(float)
    work["paid_flag"] = work["paid_flag"].astype(int)
    work["section"] = _pool_rare(work["section"], work["root"])
    work["porte"] = _pool_rare(work["porte"], work["root"])
    work["cohort"] = _pool_rare(work["cohort"], work["root"])
    work["section_ref"] = _reference(work["section"], SECTION_REFERENCE)
    work["porte_ref"] = _reference(work["porte"], SIZE_REFERENCE)
    work["cohort_ref"] = _reference(work["cohort"], str(work["cohort"].mode().iloc[0]))
    account["n_fit"] = len(work)
    account["n_firms"] = int(work["root"].nunique())
    return work, account


def _varying_terms(frame: pd.DataFrame, columns: tuple[str, ...]) -> list[str]:
    terms = []
    for column in columns:
        if frame[column].nunique(dropna=True) > 1:
            terms.append(column)
    return terms


def _categorical_term(frame: pd.DataFrame, column: str, reference: str) -> str | None:
    if frame[column].nunique(dropna=True) <= 1:
        return None
    return f"C({column}, Treatment(reference='{reference}'))"


def origin_logit_formula(frame: pd.DataFrame) -> str:
    terms = [
        "assessed",
        *_varying_terms(
            frame, ("log_stock", "debt_age", "firm_age", "firm_age_missing")
        ),
    ]
    for column, reference in (
        ("cohort", frame["cohort_ref"].iloc[0]),
        ("section", frame["section_ref"].iloc[0]),
        ("porte", frame["porte_ref"].iloc[0]),
    ):
        term = _categorical_term(frame, column, str(reference))
        if term:
            terms.append(term)
    return "paid_flag ~ " + " + ".join(terms)


def recovery_formula(frame: pd.DataFrame) -> str:
    terms = [
        "assessed",
        *_varying_terms(frame, ("debt_age", "firm_age", "firm_age_missing")),
    ]
    for column, reference in (
        ("cohort", frame["cohort_ref"].iloc[0]),
        ("section", frame["section_ref"].iloc[0]),
        ("porte", frame["porte_ref"].iloc[0]),
    ):
        term = _categorical_term(frame, column, str(reference))
        if term:
            terms.append(term)
    return "paid_scaled ~ " + " + ".join(terms)


def _fit_logit(frame: pd.DataFrame):
    groups = frame["root"].to_numpy()
    formula = origin_logit_formula(frame)
    for _attempt in range(4):
        formula = origin_logit_formula(frame)
        model = smf.logit(formula, data=frame)
        try:
            clustered = model.fit(
                disp=False,
                maxiter=200,
                cov_type="cluster",
                cov_kwds={"groups": groups},
            )
            robust = model.fit(
                disp=False,
                maxiter=200,
                cov_type="HC3",
                start_params=np.asarray(clustered.params),
            )
            return clustered, robust, formula
        except PerfectSeparationError:
            counts = frame.groupby("cohort")["root"].nunique().sort_values()
            if len(counts) <= 1:
                break
            smallest = counts.index[0]
            frame["cohort"] = frame["cohort"].where(
                frame["cohort"] != smallest, other="pooled"
            )
            frame["cohort_ref"] = _reference(
                frame["cohort"], str(frame["cohort_ref"].iloc[0])
            )
    raise RuntimeError(
        "The origin logit stayed separated after pooling sparse cohorts."
    )


def _assessed_margin(result) -> tuple[float, float, float]:
    margins = result.get_margeff(at="overall", dummy=True)
    names = list(margins.summary_frame().index)
    if "assessed" not in names:
        raise RuntimeError(f"The assessed margin is absent from {names}.")
    position = names.index("assessed")
    point = float(margins.margeff[position])
    low, high = margins.conf_int()[position]
    return point, float(low), float(high)


def _result_row(
    contrast: str,
    point: float | None,
    cluster_low: float | None,
    cluster_high: float | None,
    hc3_low: float | None,
    hc3_high: float | None,
    certificates: int,
    firms: int,
    point_name: str,
) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "Contrast": contrast,
                point_name: format_number(None if point is None else 100.0 * point)
                if point_name == "AME (pp)" or point_name == "Difference (pp)"
                else format_number(point),
                "Cluster 95% low": format_number(
                    None
                    if cluster_low is None
                    else (
                        100.0 * cluster_low
                        if point_name in {"AME (pp)", "Difference (pp)"}
                        else cluster_low
                    )
                ),
                "Cluster 95% high": format_number(
                    None
                    if cluster_high is None
                    else (
                        100.0 * cluster_high
                        if point_name in {"AME (pp)", "Difference (pp)"}
                        else cluster_high
                    )
                ),
                "HC3 95% low": format_number(
                    None
                    if hc3_low is None
                    else (
                        100.0 * hc3_low
                        if point_name in {"AME (pp)", "Difference (pp)"}
                        else hc3_low
                    )
                ),
                "HC3 95% high": format_number(
                    None
                    if hc3_high is None
                    else (
                        100.0 * hc3_high
                        if point_name in {"AME (pp)", "Difference (pp)"}
                        else hc3_high
                    )
                ),
                "Certificates": blank_count(certificates),
                "Firms": blank_count(firms),
            }
        ]
    )


def fit_origin_logit(frame: pd.DataFrame) -> tuple[pd.DataFrame, str]:
    prepared, account = prepare_population(frame)
    formula = origin_logit_formula(prepared)
    key = _signature(prepared, "logit", formula)
    cached = _read_cached(key)
    if cached is not None:
        return cached, formula
    clustered, robust, formula = _fit_logit(prepared)
    point, low, high = _assessed_margin(clustered)
    _, hc_low, hc_high = _assessed_margin(robust)
    table = _result_row(
        "Assessed (declared reference)",
        point,
        low,
        high,
        hc_low,
        hc_high,
        account["n_fit"],
        account["n_firms"],
        "AME (pp)",
    )
    _write_cached(key, table)
    return table, formula


def _design_matrix(frame: pd.DataFrame) -> tuple[pd.DataFrame, pd.Series]:
    cohort = pd.get_dummies(
        frame["cohort"].astype(str), prefix="cohort", drop_first=True
    )
    section = pd.get_dummies(
        frame["section"].astype(str), prefix="section", drop_first=True
    )
    porte = pd.get_dummies(frame["porte"].astype(str), prefix="porte", drop_first=True)
    base = frame.loc[
        :,
        ["assessed", "log_stock", "debt_age", "firm_age", "firm_age_missing"],
    ].astype(float)
    design = pd.concat(
        [
            base.reset_index(drop=True),
            cohort.reset_index(drop=True),
            section.reset_index(drop=True),
            porte.reset_index(drop=True),
        ],
        axis=1,
    ).astype(float)
    design.columns = [str(column) for column in design.columns]
    outcome = frame["paid_flag"].astype(float).reset_index(drop=True)
    return design, outcome


def _within_columns(design: pd.DataFrame, firms: pd.Series) -> pd.DataFrame:
    means = design.groupby(firms.to_numpy()).transform("mean")
    demeaned = design - means
    keep = [
        column
        for column in demeaned.columns
        if float(demeaned[column].std(ddof=0)) > 1e-8
    ]
    if "assessed" not in keep:
        raise RuntimeError(
            "Assessed origin has no within-firm variation in this sample."
        )
    return demeaned.loc[:, keep]


def fit_within_firm(frame: pd.DataFrame) -> pd.DataFrame:
    prepared, _account = prepare_population(frame)
    counts = prepared.groupby("root")["origin"].nunique()
    both = counts[counts >= 2].index
    sample = prepared.loc[prepared["root"].isin(both)].copy()
    if sample["root"].nunique() < MIN_FIRMS:
        raise RuntimeError("The within-firm sample has fewer than five firms.")
    formula = (
        "within assessed + log_stock + debt_age + firm_age + cohort + section + porte"
    )
    key = _signature(sample, "within", formula)
    cached = _read_cached(key)
    if cached is not None:
        return cached
    design, outcome = _design_matrix(sample)
    firms = sample["root"].reset_index(drop=True)
    demeaned = _within_columns(design, firms)
    y = outcome - outcome.groupby(firms.to_numpy()).transform("mean")
    codes, _uniques = pd.factorize(firms, sort=False)
    clustered = sm.OLS(y.to_numpy(), demeaned.to_numpy()).fit(
        cov_type="cluster",
        cov_kwds={"groups": codes},
    )
    robust = sm.OLS(y.to_numpy(), demeaned.to_numpy()).fit(cov_type="HC3")
    position = list(demeaned.columns).index("assessed")
    point = float(clustered.params[position])
    se = float(clustered.bse[position])
    hc_se = float(robust.bse[position])
    table = _result_row(
        "Assessed (declared reference)",
        point,
        point - Z_95 * se,
        point + Z_95 * se,
        point - Z_95 * hc_se,
        point + Z_95 * hc_se,
        len(sample),
        int(sample["root"].nunique()),
        "Difference (pp)",
    )
    _write_cached(key, table)
    return table


def fit_recovery_percent(frame: pd.DataFrame) -> tuple[pd.DataFrame, str]:
    prepared, account = prepare_population(frame)
    formula = recovery_formula(prepared)
    key = _signature(prepared, "recovery", formula)
    cached = _read_cached(key)
    if cached is not None:
        return cached, formula
    groups = prepared["root"].to_numpy()
    prepared = prepared.copy()
    prepared["paid_scaled"] = prepared["paid"].to_numpy(dtype=float) / 1000.0
    offset = np.log(prepared["stock"].to_numpy(dtype=float) / 1000.0)
    model = smf.glm(formula, data=prepared, family=sm.families.Poisson(), offset=offset)
    clustered = model.fit(
        maxiter=100,
        disp=False,
        cov_type="cluster",
        cov_kwds={"groups": groups},
    )
    robust = model.fit(
        maxiter=100,
        disp=False,
        cov_type="HC3",
        start_params=np.asarray(clustered.params),
    )
    beta = float(clustered.params["assessed"])
    point, low, high = percent_difference(beta, float(clustered.bse["assessed"]))
    _hc_point, hc_low, hc_high = percent_difference(beta, float(robust.bse["assessed"]))
    table = _result_row(
        "Assessed (declared reference)",
        point,
        low,
        high,
        hc_low,
        hc_high,
        account["n_fit"],
        account["n_firms"],
        "Percent difference",
    )
    _write_cached(key, table)
    return table, formula


def _interval_figure(
    table: pd.DataFrame, point_name: str, axis_title: str
) -> go.Figure:
    assert_public_frame(table)
    row = table.iloc[0]
    point = float(str(row[point_name]).replace(",", ""))
    estimators = ["Cluster", "HC3"]
    lows = [
        float(str(row["Cluster 95% low"]).replace(",", "")),
        float(str(row["HC3 95% low"]).replace(",", "")),
    ]
    highs = [
        float(str(row["Cluster 95% high"]).replace(",", "")),
        float(str(row["HC3 95% high"]).replace(",", "")),
    ]
    figure = go.Figure()
    figure.add_trace(
        go.Scatter(
            x=[point, point],
            y=estimators,
            mode="markers",
            marker={"size": 12, "color": "#154C56"},
            error_x={
                "type": "data",
                "array": [highs[0] - point, highs[1] - point],
                "arrayminus": [point - lows[0], point - lows[1]],
                "color": "#154C56",
                "thickness": 1.4,
                "width": 6,
            },
            name="Assessed vs declared",
            hovertemplate="%{y}: %{x:.2f}<extra></extra>",
        )
    )
    figure.add_vline(x=0, line_dash="dot", line_color="#8A9499")
    figure.update_layout(
        xaxis_title=axis_title,
        yaxis_title="Variance estimator",
        showlegend=False,
        template="simple_white",
        font={"family": "Arial, sans-serif", "size": 12, "color": "#154C56"},
        margin={"l": 80, "r": 20, "t": 30, "b": 50},
        paper_bgcolor="white",
        plot_bgcolor="white",
    )
    for trace in figure.data:
        if "gl" in str(trace.type):
            raise RuntimeError("WebGL traces are not used.")
    return figure


def write_figure(figure: go.Figure, stem: str, table: pd.DataFrame) -> None:
    assert_public_frame(table)
    figures, _tables = manuscript_dirs()
    csv_path = figures / f"{stem}.csv"
    table.to_csv(csv_path, index=False)
    png_path = figures / f"{stem}.png"
    svg_path = figures / f"{stem}.svg"
    try:
        figure.write_image(png_path, width=700, height=320, scale=4)
        figure.write_image(svg_path, width=700, height=320)
    except (ImportError, ModuleNotFoundError, OSError, RuntimeError, ValueError):
        svg_path.write_text(_fallback_svg(table), encoding="utf-8")
        if not png_path.exists():
            _write_blank_png(png_path)


def _fallback_svg(table: pd.DataFrame) -> str:
    """Axis frame used when the static image export is unavailable."""
    return (
        "<?xml version='1.0' encoding='UTF-8'?>\n"
        "<svg xmlns='http://www.w3.org/2000/svg' width='700' height='320'>\n"
        "<rect width='700' height='320' fill='white'/>\n"
        "<text x='40' y='40' font-family='Arial' font-size='14' fill='#154C56'>"
        "Estimate is written by the supplementary material.</text>\n"
        f"<text x='40' y='70' font-family='Arial' font-size='12' fill='#154C56'>"
        f"{_tex_escape(table.columns[1])}</text>\n"
        "</svg>\n"
    )


def _write_blank_png(path: Path) -> None:
    import struct
    import zlib

    width, height = 700, 320
    raw = b"".join(b"\x00" + b"\xff\xff\xff" * width for _row in range(height))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )
    path.write_bytes(png)


def export_origin_logit(table: pd.DataFrame) -> Path:
    _figures, tables = manuscript_dirs()
    path = tables / "tab_logit_origin_ame.tex"
    write_table(
        table,
        path,
        "Average marginal effect of assessed origin on any payment.",
        "tab:logit-ame",
        CONTROLS_NOTE,
    )
    figure = _interval_figure(
        table,
        "AME (pp)",
        "Average marginal effect of assessed origin (percentage points)",
    )
    write_figure(figure, "fig_logit_origin_ame", table)
    return path


def export_within(table: pd.DataFrame) -> Path:
    _figures, tables = manuscript_dirs()
    path = tables / "tab_within_firm_logit.tex"
    write_table(
        table,
        path,
        "Within-firm difference in the probability of any payment.",
        "tab:within-firm",
        WITHIN_NOTE,
    )
    figure = _interval_figure(
        table,
        "Difference (pp)",
        "Within-firm difference in the probability of any payment (percentage points)",
    )
    write_figure(figure, "fig_within_firm", table)
    return path


def export_recovery(table: pd.DataFrame) -> Path:
    _figures, tables = manuscript_dirs()
    path = tables / "tab_recovery_pct.tex"
    write_table(
        table,
        path,
        "Percentage difference in expected payments per real of stock.",
        "tab:recovery-pct",
        RECOVERY_NOTE,
    )
    figure = _interval_figure(
        table,
        "Percent difference",
        "Percentage difference in expected payments per real of stock",
    )
    write_figure(figure, "fig_recovery_pct", table)
    return path


def run_origin_logit(connection=None) -> pd.DataFrame:
    frame = load_analysis_frame(connection)
    table, _formula = fit_origin_logit(frame)
    export_origin_logit(table)
    return table


def run_within_firm(connection=None) -> pd.DataFrame:
    frame = load_analysis_frame(connection)
    table = fit_within_firm(frame)
    export_within(table)
    return table


def run_recovery(connection=None) -> pd.DataFrame:
    frame = load_analysis_frame(connection)
    table, _formula = fit_recovery_percent(frame)
    export_recovery(table)
    return table


def run_all(connection=None) -> dict[str, pd.DataFrame]:
    return {
        "logit": run_origin_logit(connection),
        "within": run_within_firm(connection),
        "recovery": run_recovery(connection),
    }


if __name__ == "__main__":
    run_all()
