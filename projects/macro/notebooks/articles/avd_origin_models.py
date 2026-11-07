"""Origin models for declared and assessed VAT (ICMS) certificates.

The certificate is the unit. The firm key is the eight-digit root and is used
only to cluster or to demean. It is never written. NOME_DEVEDOR and
CPF_DEVEDOR are never read. Counts from 1 to 4 are left blank.

Before each fit the script logs the design rank and any sparse or
non-varying dummy, by column name and count. An exactly collinear column
is dropped. If debt age is perfectly collinear with the enrolment year, the
year is kept and debt age is dropped. A CNAE section with fewer than five
firms is collapsed into other. A cohort year or a CNAE section with fewer
than 20 certificates, or with a constant payment indicator, is merged
before the fit. The likelihood is fit once. Cluster and HC3 covariances
are computed from those parameters, with HC3 leverage one row at a time.
The extracted frame is cached under .local/cache/. The three models fail
independently.
"""

from __future__ import annotations

import argparse
import gc
import hashlib
import logging
import os
import resource
import sys
import tempfile
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import patsy
import plotly.graph_objects as go
import statsmodels.api as sm
from statsmodels.tools.sm_exceptions import ConvergenceWarning, PerfectSeparationError

logger = logging.getLogger("avd_origin_models")

# Result tables change when the fit changes. The extracted frame does not.
RESULT_CACHE_VERSION = "avd-origin-3"
FRAME_CACHE_VERSION = "avd-origin-2"
PAY_START = "2016-01-01"
PAY_END = "2026-01-01"
AS_OF = "2025-12-31"
ENROL_MIN = "1960-01-01"
OPEN_MIN = "1900-01-01"
Z_95 = 1.959963984540054
MIN_FIRMS = 5
# A CNAE section with fewer than this many distinct firm keys is collapsed into CNAE_OTHER.
CNAE_MIN_FIRMS = 5
CNAE_OTHER = "other"
# Logged before the fit. Merging uses MIN_LEVEL_OBS, not this logging threshold.
MIN_DUMMY_OBS = 5
# A cohort year or a CNAE section with fewer than this many certificates, or
# with a constant payment indicator, is merged before the fit.
MIN_LEVEL_OBS = 20
LOGIT_MAXITER = 500
COV_CHUNK = 16384
# Relative QR tolerance for an exact linear dependence. It is not a significance cutoff.
COLLINEAR_TOL = 1e-8
SECTION_REFERENCE = "G"
SIZE_REFERENCE = "01"
FRAME_CACHE_NAME = "avd_origin_frame.parquet"
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
    "A CNAE section with fewer than five firms is collapsed into other. "
    "A cohort year or a CNAE section with fewer than 20 certificates, "
    "or with a constant payment indicator, is merged before the fit. "
    "Debt age is omitted when it is perfectly collinear with the enrolment year. "
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
    "A CNAE section with fewer than five firms is collapsed into other. "
    "A cohort year or a CNAE section with fewer than 20 certificates, "
    "or with a constant payment indicator, is merged before the fit. "
    "Debt age is omitted when it is perfectly collinear with the enrolment year. "
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
    "A CNAE section with fewer than five firms is collapsed into other. "
    "A cohort year or a CNAE section with fewer than 20 certificates, "
    "or with a constant payment indicator, is merged before the fit. "
    "Debt age is omitted when it is perfectly collinear with the enrolment year. "
    "The main interval is clustered by the firm. "
    "HC3 is the robustness column of the within regression. "
    "A count from 1 to 4 is left blank. The firm key is not printed."
)

_FRAME: pd.DataFrame | None = None


def _configure_logging() -> None:
    logger.setLevel(logging.INFO)
    if logger.handlers or logging.getLogger().handlers:
        return
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )


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
    digest.update(RESULT_CACHE_VERSION.encode())
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


def load_analysis_frame(connection=None, *, from_cache: bool = False) -> pd.DataFrame:
    """Load the comparison population. The frame stays in memory."""
    global _FRAME
    _configure_logging()
    if from_cache:
        _FRAME = _read_frame_cache()
        logger.info("Loaded the analysis frame from the local cache.")
        return _FRAME
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
    save_analysis_frame(frame)
    _FRAME = frame
    return _FRAME


def clear_frame() -> None:
    global _FRAME
    _FRAME = None


def frame_cache_path(repo: Path | None = None) -> Path:
    """Local parquet of the extracted frame. The directory is gitignored."""
    root = repo or find_repo()
    return root / ".local" / "cache" / FRAME_CACHE_NAME


def save_analysis_frame(frame: pd.DataFrame, repo: Path | None = None) -> Path:
    """Write the extracted model frame. The file stays on this machine."""
    _configure_logging()
    missing = [column for column in FRAME_COLUMNS if column not in frame.columns]
    if missing:
        raise RuntimeError(f"The analysis frame is missing {missing}.")
    extra = [column for column in frame.columns if column not in FRAME_COLUMNS]
    if extra:
        raise RuntimeError(f"The analysis frame has unexpected columns {extra}.")
    path = frame_cache_path(repo)
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.loc[:, list(FRAME_COLUMNS)].to_parquet(path, index=False)
    path.with_suffix(".version").write_text(
        FRAME_CACHE_VERSION + "\n", encoding="utf-8"
    )
    logger.info("Cached the analysis frame.")
    return path


def _read_frame_cache() -> pd.DataFrame:
    path = frame_cache_path()
    version_path = path.with_suffix(".version")
    if not path.exists() or not version_path.exists():
        raise RuntimeError(
            "No cached analysis frame. Run the script once without --from-cache."
        )
    version = version_path.read_text(encoding="utf-8").strip()
    if version != FRAME_CACHE_VERSION:
        raise RuntimeError(
            "The cached analysis frame is from another model version. "
            "Run the script once without --from-cache."
        )
    frame = pd.read_parquet(path)
    missing = [column for column in FRAME_COLUMNS if column not in frame.columns]
    if missing:
        raise RuntimeError(f"The cached analysis frame is missing {missing}.")
    extra = [column for column in frame.columns if column not in FRAME_COLUMNS]
    if extra:
        raise RuntimeError(f"The cached analysis frame has unexpected columns {extra}.")
    return frame.loc[:, list(FRAME_COLUMNS)]


def _pool_rare(
    levels: pd.Series,
    firms: pd.Series,
    min_firms: int = MIN_FIRMS,
    replacement: str = "pooled",
    log_label: str | None = None,
) -> pd.Series:
    table = pd.DataFrame({"level": levels.astype(str), "firm": firms})
    counts = table.groupby("level")["firm"].nunique()
    rare = set(counts[counts < min_firms].index)
    if log_label and rare:
        logger.info(
            "%s levels collapsed into %s: levels=%s",
            log_label,
            replacement,
            len(rare),
        )
    if not rare:
        return levels.astype(str)
    return levels.astype(str).where(~levels.astype(str).isin(rare), other=replacement)


def _reference(levels: pd.Series, preferred: str) -> str:
    present = set(levels.astype(str))
    if preferred in present:
        return preferred
    mode = levels.astype(str).mode(dropna=True)
    if mode.empty:
        raise RuntimeError("A categorical control has no level.")
    chosen = str(mode.iloc[0])
    logger.info(
        "Reference %s is absent. The modal level %s is used.",
        preferred,
        chosen,
    )
    return chosen


def _level_status(levels: pd.Series, outcome: pd.Series) -> pd.DataFrame:
    table = pd.DataFrame(
        {"level": levels.astype(str).to_numpy(), "y": np.asarray(outcome)}
    )
    return table.groupby("level", sort=False)["y"].agg(
        observations="size", outcome_distinct="nunique"
    )


def _unstable_levels(status: pd.DataFrame) -> list[str]:
    unstable = (status["observations"] < MIN_LEVEL_OBS) | (
        status["outcome_distinct"] <= 1
    )
    return [str(level) for level in status.index[unstable]]


def _format_level(level: str, status: pd.DataFrame) -> str:
    if level not in status.index:
        return level
    count = int(status.loc[level, "observations"])
    if 1 <= count <= 4:
        return f"{level} (n suppressed)"
    return f"{level} (n={count})"


def _observed_years(levels: pd.Series) -> list[int]:
    found = {int(value) for value in levels.unique() if str(value).isdigit()}
    return sorted(found)


def _year_runs(years: list[int]) -> list[tuple[int, int]]:
    if not years:
        return []
    runs = []
    start = previous = years[0]
    for year in years[1:]:
        if year == previous + 1:
            previous = year
            continue
        runs.append((start, previous))
        start = previous = year
    runs.append((start, previous))
    return runs


def _values_vary(levels: np.ndarray, outcome: np.ndarray, members: set[str]) -> bool:
    chosen = np.isin(levels, list(members))
    if not chosen.any():
        return False
    return int(np.unique(outcome[chosen]).size) > 1


def _merge_cohorts(levels: pd.Series, outcome: pd.Series) -> pd.Series:
    """Merge thin or separating cohort years into a contiguous bin."""
    original_index = levels.index
    labels = levels.astype(str).to_numpy()
    payments = np.asarray(outcome)
    status = _level_status(levels, outcome)
    unstable_years = sorted(
        int(level) for level in _unstable_levels(status) if str(level).isdigit()
    )
    mapping: dict[str, str] = {}
    for level in _unstable_levels(status):
        if not str(level).isdigit():
            mapping[str(level)] = "pooled"
    observed = _observed_years(pd.Series(labels))
    for start, end in _year_runs(unstable_years):
        members = list(range(start, end + 1))
        while not _values_vary(labels, payments, {str(year) for year in members}):
            later = [year for year in observed if year > members[-1]]
            if not later:
                break
            members.append(later[0])
        member_labels = {str(year) for year in members}
        if _values_vary(labels, payments, member_labels):
            if observed and members[0] == observed[0]:
                label = f"<={members[-1]}"
            elif len(members) == 1:
                label = "pooled"
            else:
                label = f"{members[0]}-{members[-1]}"
        else:
            outside = labels[~np.isin(labels, list(member_labels))]
            if outside.size == 0:
                continue
            label = str(pd.Series(outside).mode().iloc[0])
        for year in members:
            mapping[str(year)] = label
        logger.info(
            "Merged cohort levels %s into %s.",
            ", ".join(_format_level(str(year), status) for year in members),
            label,
        )
    merged = pd.Series(labels, index=original_index)
    if not mapping:
        return merged
    return merged.replace(mapping)


def _merge_sections(levels: pd.Series, outcome: pd.Series, reference: str) -> pd.Series:
    """Merge thin or separating CNAE sections into other."""
    original_index = levels.index
    labels = levels.astype(str)
    status = _level_status(labels, outcome)
    unstable = [
        level
        for level in _unstable_levels(status)
        if level not in {reference, CNAE_OTHER}
    ]
    mapped = labels.where(~labels.isin(unstable), CNAE_OTHER)
    if unstable:
        logger.info(
            "Merged CNAE levels %s into %s.",
            ", ".join(_format_level(level, status) for level in sorted(unstable)),
            CNAE_OTHER,
        )
    other = mapped == CNAE_OTHER
    payments = np.asarray(outcome)
    if (
        bool(other.to_numpy().any())
        and int(np.unique(payments[other.to_numpy()]).size) <= 1
    ):
        logger.info(
            "CNAE bin %s has a constant payment indicator. It takes the reference %s.",
            CNAE_OTHER,
            reference,
        )
        mapped = mapped.where(~other, reference)
    mapped.index = original_index
    return mapped


def prepare_population(frame: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, int]]:
    """Drop rows that cannot enter the clustered logit, and pool sparse categories."""
    _configure_logging()
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
    work["section"] = _pool_rare(
        work["section"],
        work["root"],
        min_firms=CNAE_MIN_FIRMS,
        replacement=CNAE_OTHER,
        log_label="CNAE",
    )
    work["porte"] = _pool_rare(work["porte"], work["root"])
    work["section"] = _merge_sections(
        work["section"], work["paid_flag"], SECTION_REFERENCE
    )
    work["cohort"] = _merge_cohorts(work["cohort"], work["paid_flag"])
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


def _keep_priority(name: str) -> tuple[int, str]:
    """Columns earlier in this order are kept when two columns are collinear."""
    lowered = name.lower()
    if name == "Intercept":
        return (0, name)
    if lowered == "assessed":
        return (1, name)
    if "cohort" in lowered:
        return (2, name)
    if lowered == "log_stock":
        return (3, name)
    if "section" in lowered:
        return (4, name)
    if "porte" in lowered:
        return (5, name)
    if lowered in {"firm_age", "firm_age_missing"}:
        return (6, name)
    if lowered == "debt_age":
        return (8, name)
    return (7, name)


def _dummy_ones(column: np.ndarray) -> int | None:
    finite = column[np.isfinite(column)]
    if finite.size == 0:
        return 0
    rounded = np.round(finite, 10)
    if not np.isin(rounded, (0.0, 1.0)).all():
        return None
    return int(np.sum(rounded == 1.0))


def preflight_design(
    outcome: pd.Series,
    design: pd.DataFrame,
    label: str,
    rank: int | None = None,
) -> None:
    """Log rank and sparse or non-varying dummies. Names and counts only."""
    _configure_logging()
    if rank is None:
        matrix = design.to_numpy(dtype=float)
        rank = int(np.linalg.matrix_rank(matrix)) if matrix.size else 0
    logger.info("%s: rank %s of %s columns", label, rank, design.shape[1])
    y = outcome.to_numpy(dtype=float)
    for name in design.columns:
        if str(name) == "Intercept":
            continue
        values = design[name].to_numpy(dtype=float)
        finite = values[np.isfinite(values)]
        if finite.size == 0 or np.unique(np.round(finite, 10)).size <= 1:
            logger.info(
                "column %s: observations=%s",
                name,
                int(np.isfinite(values).sum()),
            )
            continue
        ones = _dummy_ones(values)
        if ones is None:
            continue
        mask = np.round(values, 10) == 1.0
        distinct = int(np.unique(y[mask]).size) if int(mask.sum()) else 0
        if ones < MIN_DUMMY_OBS or distinct <= 1:
            logger.info(
                "dummy %s: observations=%s outcome_distinct=%s",
                name,
                ones,
                distinct,
            )


def reduce_design(design: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """Drop columns that are exact linear combinations of higher-priority columns."""
    order = sorted((str(column) for column in design.columns), key=_keep_priority)
    matrix = np.asfortranarray(design.loc[:, order].to_numpy(dtype=float))
    if matrix.size == 0 or matrix.shape[1] == 0:
        return design.iloc[:, 0:0], []
    _q, r_matrix = np.linalg.qr(matrix, mode="reduced")
    diagonal = np.zeros(matrix.shape[1], dtype=float)
    diag_r = np.abs(np.diag(r_matrix))
    diagonal[: diag_r.shape[0]] = diag_r
    norms = np.linalg.norm(matrix, axis=0)
    keep: list[str] = []
    dropped: list[str] = []
    for index, name in enumerate(order):
        scale = float(norms[index])
        independent = scale > 0.0 and float(diagonal[index]) > COLLINEAR_TOL * scale
        if independent:
            keep.append(name)
        else:
            dropped.append(name)
    if not keep:
        raise RuntimeError("Every design column was collinear.")
    return design.loc[:, keep], dropped


def _log_collinear(dropped: list[str], kept: list[str]) -> None:
    if not dropped:
        return
    logger.info("Dropped exactly collinear columns: %s", ", ".join(dropped))
    kept_cohort = any("cohort" in name.lower() for name in kept)
    if "debt_age" in dropped and kept_cohort:
        logger.info(
            "debt_age is perfectly collinear with the enrolment-year indicators. "
            "The enrolment year is kept and debt_age is dropped."
        )


def _fitted_formula(outcome: str, columns: pd.Index | list[str]) -> str:
    terms = [str(column) for column in columns if str(column) != "Intercept"]
    if not terms:
        raise RuntimeError(
            f"The {outcome} model has no regressor after the collinearity check."
        )
    return outcome + " ~ " + " + ".join(terms)


def _memory_mb() -> tuple[float, float]:
    """Current and peak resident set, in mebibytes."""
    current = None
    try:
        with open("/proc/self/status", encoding="ascii") as handle:
            for line in handle:
                if line.startswith("VmRSS:"):
                    current = int(line.split()[1]) / 1024.0
                    break
    except OSError:
        current = None
    peak_raw = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    if sys.platform == "darwin":
        peak = peak_raw / (1024.0 * 1024.0)
    else:
        peak = peak_raw / 1024.0
    if current is None:
        current = peak
    return current, peak


def _log_memory(label: str) -> None:
    _configure_logging()
    current, peak = _memory_mb()
    logger.info("%s RSS current %.0f MB, peak %.0f MB", label, current, peak)


def _iter_blocks(n_rows: int):
    for start in range(0, n_rows, COV_CHUNK):
        yield start, min(start + COV_CHUNK, n_rows)


def _weighted_gram(exog: np.ndarray, weight: np.ndarray) -> np.ndarray:
    width = exog.shape[1]
    gram = np.zeros((width, width), dtype=np.float64)
    for start, stop in _iter_blocks(exog.shape[0]):
        block = exog[start:stop]
        weighted = weight[start:stop, None] * block
        gram += weighted.T @ block
    return gram


def _row_quadratic(exog: np.ndarray, bread: np.ndarray) -> np.ndarray:
    """Diagonal of exog @ bread @ exog.T, one chunk at a time."""
    diagonal = np.empty(exog.shape[0], dtype=np.float64)
    for start, stop in _iter_blocks(exog.shape[0]):
        block = exog[start:stop]
        diagonal[start:stop] = np.einsum("ij,ij->i", block @ bread, block)
    return diagonal


def _meat(exog: np.ndarray, scale: np.ndarray) -> np.ndarray:
    width = exog.shape[1]
    meat = np.zeros((width, width), dtype=np.float64)
    for start, stop in _iter_blocks(exog.shape[0]):
        block = exog[start:stop]
        meat += (scale[start:stop, None] * block).T @ block
    return meat


def _cluster_cov(
    exog: np.ndarray, score: np.ndarray, codes: np.ndarray, bread: np.ndarray
) -> np.ndarray:
    n_groups = int(codes.max()) + 1 if codes.size else 0
    width = exog.shape[1]
    sums = np.zeros((n_groups, width), dtype=np.float64)
    for start, stop in _iter_blocks(exog.shape[0]):
        block = exog[start:stop]
        np.add.at(sums, codes[start:stop], score[start:stop, None] * block)
    meat = sums.T @ sums
    del sums
    cov = bread @ meat @ bread
    n_obs = exog.shape[0]
    if n_groups > 1 and n_obs > width:
        cov *= n_groups / (n_groups - 1.0) * ((n_obs - 1.0) / (n_obs - width))
    return cov


def _hc3_cov(
    exog: np.ndarray,
    residual: np.ndarray,
    bread: np.ndarray,
    weight: np.ndarray | None = None,
) -> np.ndarray:
    """MacKinnon-White HC3. Leverage is a length-n vector, never an n by n matrix."""
    leverage = _row_quadratic(exog, bread)
    if weight is not None:
        leverage = weight * leverage
    leverage = np.minimum(leverage, 1.0 - 1e-8)
    scale = (residual / (1.0 - leverage)) ** 2
    return bread @ _meat(exog, scale) @ bread


def _group_codes(roots: np.ndarray) -> np.ndarray:
    codes, uniques = pd.factorize(roots, sort=False)
    del uniques
    return codes.astype(np.int32, copy=False)


def _converged(result) -> bool:
    retvals = getattr(result, "mle_retvals", None) or {}
    if "converged" in retvals:
        return bool(retvals["converged"])
    return bool(getattr(result, "converged", False))


def _require_converged(result, label: str) -> None:
    if _converged(result):
        return
    logger.info("The %s fit did not converge.", label)
    raise RuntimeError(f"The {label} fit did not converge.")


def _fit_with_fallback(model, label: str):
    """One Newton fit, then BFGS from those parameters if Newton does not converge."""
    result = None
    start = None
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", ConvergenceWarning)
            result = model.fit(disp=False, method="newton", maxiter=LOGIT_MAXITER)
    except (np.linalg.LinAlgError, PerfectSeparationError) as exc:
        logger.info(
            "%s Newton failed (%s). Retrying with BFGS.",
            label,
            type(exc).__name__,
        )
    if result is not None and _converged(result):
        return result
    if result is not None:
        logger.info("%s Newton did not converge. Retrying with BFGS.", label)
        start = np.asarray(result.params, dtype=np.float64)
        del result
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", ConvergenceWarning)
            result = model.fit(
                disp=False,
                method="bfgs",
                maxiter=LOGIT_MAXITER,
                start_params=start,
            )
    except (np.linalg.LinAlgError, PerfectSeparationError) as exc:
        logger.info("%s BFGS failed (%s).", label, type(exc).__name__)
        raise RuntimeError(f"The {label} fit did not converge.") from exc
    _require_converged(result, label)
    return result


def _expit(linear: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(linear, -50.0, 50.0)))


def _assessed_ame(
    exog: np.ndarray, names: list[str], beta: np.ndarray, cov: np.ndarray
) -> tuple[float, float, float]:
    """Finite-difference average marginal effect of assessed origin, with delta-method interval."""
    if "assessed" not in names:
        raise RuntimeError("The assessed margin is absent from the design.")
    position = names.index("assessed")
    total = 0.0
    gradient = np.zeros(exog.shape[1], dtype=np.float64)
    n_rows = 0
    for start, stop in _iter_blocks(exog.shape[0]):
        block = exog[start:stop]
        linear = block @ beta
        base = linear - block[:, position] * beta[position]
        probability_one = _expit(base + beta[position])
        probability_zero = _expit(base)
        total += float(np.sum(probability_one - probability_zero))
        weight_one = probability_one * (1.0 - probability_one)
        weight_gap = weight_one - probability_zero * (1.0 - probability_zero)
        cleared = block.copy()
        cleared[:, position] = 0.0
        gradient += weight_gap @ cleared
        gradient[position] += float(np.sum(weight_one))
        n_rows += stop - start
    point = total / n_rows
    gradient /= n_rows
    variance = float(gradient @ cov @ gradient)
    standard_error = float(np.sqrt(max(variance, 0.0)))
    return point, point - Z_95 * standard_error, point + Z_95 * standard_error


def _log_sparse_matrix(
    outcome: np.ndarray, matrix: np.ndarray, names: list[str], label: str
) -> None:
    _configure_logging()
    y = np.asarray(outcome, dtype=np.float64)
    for index, name in enumerate(names):
        if name == "Intercept":
            continue
        values = matrix[:, index]
        finite = values[np.isfinite(values)]
        if finite.size == 0 or np.unique(np.round(finite, 10)).size <= 1:
            logger.info(
                "column %s: observations=%s",
                name,
                int(np.isfinite(values).sum()),
            )
            continue
        ones = _dummy_ones(values)
        if ones is None:
            continue
        mask = np.round(values, 10) == 1.0
        distinct = int(np.unique(y[mask]).size) if int(mask.sum()) else 0
        if ones < MIN_DUMMY_OBS or distinct <= 1:
            logger.info(
                "dummy %s: observations=%s outcome_distinct=%s",
                name,
                ones,
                distinct,
            )


def _order_exog(
    matrix: np.ndarray, names: list[str]
) -> tuple[np.ndarray, list[str], list[int], list[str]]:
    """QR the design in priority order. The caller drops the original matrix next."""
    order = sorted(range(len(names)), key=lambda index: _keep_priority(names[index]))
    ordered = np.asfortranarray(matrix[:, order])
    _q, r_matrix = np.linalg.qr(ordered, mode="reduced")
    del _q
    diagonal = np.zeros(ordered.shape[1], dtype=np.float64)
    diag_r = np.abs(np.diag(r_matrix))
    diagonal[: diag_r.shape[0]] = diag_r
    norms = np.linalg.norm(ordered, axis=0)
    keep_positions: list[int] = []
    dropped: list[str] = []
    ordered_names = [names[index] for index in order]
    for index, name in enumerate(ordered_names):
        scale = float(norms[index])
        independent = scale > 0.0 and float(diagonal[index]) > COLLINEAR_TOL * scale
        if independent:
            keep_positions.append(index)
        else:
            dropped.append(name)
    if not keep_positions:
        raise RuntimeError("Every design column was collinear.")
    return ordered, ordered_names, keep_positions, dropped


def _compact_exog(
    ordered: np.ndarray, ordered_names: list[str], keep_positions: list[int]
) -> tuple[np.ndarray, list[str]]:
    kept_names = [ordered_names[index] for index in keep_positions]
    kept = np.ascontiguousarray(ordered[:, keep_positions])
    return kept, kept_names


def _design_arrays(
    formula: str, frame: pd.DataFrame
) -> tuple[np.ndarray, np.ndarray, list[str], np.ndarray]:
    work = frame.reset_index(drop=True)
    outcome, design = patsy.dmatrices(formula, work, return_type="dataframe")
    if len(design) != len(work):
        raise RuntimeError("The design dropped rows. Refusing to fit.")
    names = [str(column) for column in design.columns]
    y = np.ascontiguousarray(outcome.iloc[:, 0].to_numpy(dtype=np.float64))
    matrix = np.ascontiguousarray(design.to_numpy(dtype=np.float64))
    codes = _group_codes(work["root"].to_numpy())
    del outcome, design, work
    return y, matrix, names, codes


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
    outcome, matrix, names, codes = _design_arrays(formula, prepared)
    _log_sparse_matrix(outcome, matrix, names, "origin logit")
    ordered, ordered_names, keep_positions, dropped = _order_exog(matrix, names)
    del matrix
    gc.collect()
    reduced, reduced_names = _compact_exog(ordered, ordered_names, keep_positions)
    del ordered
    gc.collect()
    logger.info("origin logit: rank %s of %s columns", len(reduced_names), len(names))
    _log_collinear(dropped, reduced_names)
    fitted = formula if not dropped else _fitted_formula("paid_flag", reduced_names)
    model = sm.Logit(outcome, reduced)
    result = _fit_with_fallback(model, "origin logit")
    beta = np.asarray(result.params, dtype=np.float64)
    probability = np.asarray(result.predict(), dtype=np.float64)
    del result, model
    weight = probability * (1.0 - probability)
    bread = np.linalg.inv(_weighted_gram(reduced, weight))
    score = outcome - probability
    cluster_cov = _cluster_cov(reduced, score, codes, bread)
    hc3_cov = _hc3_cov(reduced, score, bread, weight)
    point, low, high = _assessed_ame(reduced, reduced_names, beta, cluster_cov)
    _point, hc_low, hc_high = _assessed_ame(reduced, reduced_names, beta, hc3_cov)
    del outcome, reduced, probability, weight, score, codes, beta, cluster_cov, hc3_cov
    gc.collect()
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
    return table, fitted


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
    demeaned, dropped = reduce_design(demeaned)
    _log_collinear(dropped, list(demeaned.columns))
    if "assessed" not in demeaned.columns:
        raise RuntimeError(
            "Assessed origin has no within-firm variation in this sample."
        )
    preflight_design(outcome, design, "within-firm", rank=demeaned.shape[1])
    y = (outcome - outcome.groupby(firms.to_numpy()).transform("mean")).to_numpy(
        dtype=np.float64
    )
    exog = np.ascontiguousarray(demeaned.to_numpy(dtype=np.float64))
    names = list(demeaned.columns)
    del design, demeaned, outcome
    beta, *_ = np.linalg.lstsq(exog, y, rcond=None)
    residual = y - exog @ beta
    bread = np.linalg.inv(exog.T @ exog)
    codes = _group_codes(firms.to_numpy())
    cluster_cov = _cluster_cov(exog, residual, codes, bread)
    hc3_cov = _hc3_cov(exog, residual, bread)
    position = names.index("assessed")
    point = float(beta[position])
    se = float(np.sqrt(cluster_cov[position, position]))
    hc_se = float(np.sqrt(hc3_cov[position, position]))
    del exog, y, residual, codes, beta, cluster_cov, hc3_cov
    gc.collect()
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
    prepared = prepared.copy()
    prepared["paid_scaled"] = prepared["paid"].to_numpy(dtype=np.float64) / 1000.0
    outcome, matrix, names, codes = _design_arrays(formula, prepared)
    _log_sparse_matrix(outcome, matrix, names, "recovery")
    ordered, ordered_names, keep_positions, dropped = _order_exog(matrix, names)
    del matrix
    gc.collect()
    reduced, reduced_names = _compact_exog(ordered, ordered_names, keep_positions)
    del ordered
    gc.collect()
    logger.info("recovery: rank %s of %s columns", len(reduced_names), len(names))
    _log_collinear(dropped, reduced_names)
    formula = formula if not dropped else _fitted_formula("paid_scaled", reduced_names)
    offset = np.log(prepared["stock"].to_numpy(dtype=np.float64) / 1000.0)
    model = sm.GLM(
        outcome,
        reduced,
        family=sm.families.Poisson(),
        offset=offset,
    )
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", ConvergenceWarning)
        result = model.fit(maxiter=LOGIT_MAXITER, disp=False)
    if not _converged(result):
        logger.info(
            "The recovery model did not converge. Retrying from the last parameters."
        )
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", ConvergenceWarning)
            result = model.fit(
                maxiter=LOGIT_MAXITER,
                disp=False,
                start_params=np.asarray(result.params, dtype=np.float64),
            )
    _require_converged(result, "recovery")
    beta_vector = np.asarray(result.params, dtype=np.float64)
    mean = np.asarray(result.predict(), dtype=np.float64)
    del result, model
    score = outcome - mean
    bread = np.linalg.inv(_weighted_gram(reduced, mean))
    cluster_cov = _cluster_cov(reduced, score, codes, bread)
    hc3_cov = _hc3_cov(reduced, score, bread, mean)
    position = reduced_names.index("assessed")
    beta = float(beta_vector[position])
    point, low, high = percent_difference(
        beta, float(np.sqrt(cluster_cov[position, position]))
    )
    _hc_point, hc_low, hc_high = percent_difference(
        beta, float(np.sqrt(hc3_cov[position, position]))
    )
    del outcome, reduced, mean, score, codes, offset, beta_vector, cluster_cov, hc3_cov
    gc.collect()
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


def run_origin_logit(
    connection=None, frame: pd.DataFrame | None = None
) -> pd.DataFrame:
    if frame is None:
        frame = load_analysis_frame(connection)
    table, _formula = fit_origin_logit(frame)
    export_origin_logit(table)
    return table


def run_within_firm(connection=None, frame: pd.DataFrame | None = None) -> pd.DataFrame:
    if frame is None:
        frame = load_analysis_frame(connection)
    table = fit_within_firm(frame)
    export_within(table)
    return table


def run_recovery(connection=None, frame: pd.DataFrame | None = None) -> pd.DataFrame:
    if frame is None:
        frame = load_analysis_frame(connection)
    table, _formula = fit_recovery_percent(frame)
    export_recovery(table)
    return table


def run_all(
    connection=None,
    *,
    from_cache: bool = False,
    frame: pd.DataFrame | None = None,
    only: str | None = None,
) -> dict[str, pd.DataFrame]:
    """Fit the three models. One failure does not skip the other outputs."""
    _configure_logging()
    if frame is None:
        frame = load_analysis_frame(connection, from_cache=from_cache)
    results: dict[str, pd.DataFrame] = {}
    failed: list[str] = []
    runners = (
        ("logit", run_origin_logit),
        ("within", run_within_firm),
        ("recovery", run_recovery),
    )
    if only is not None:
        runners = tuple(item for item in runners if item[0] == only)
        if not runners:
            raise RuntimeError("Unknown model. Choose logit, within or recovery.")
    for name, runner in runners:
        gc.collect()
        _log_memory(f"{name} start")
        try:
            results[name] = runner(frame=frame)
        except Exception:
            logger.exception("The %s model failed.", name)
            failed.append(name)
        gc.collect()
        _log_memory(f"{name} end")
    if failed:
        written = ", ".join(results) if results else "none"
        raise RuntimeError(
            "Failed models: " + ", ".join(failed) + f". Written models: {written}."
        )
    return results


def main(argv: list[str] | None = None) -> dict[str, pd.DataFrame]:
    parser = argparse.ArgumentParser(description="Fit the origin models.")
    parser.add_argument(
        "--from-cache",
        action="store_true",
        help="Read the analysis frame from .local/cache and skip extraction.",
    )
    parser.add_argument(
        "--only",
        choices=("logit", "within", "recovery"),
        help="Fit one model and skip the others.",
    )
    args = parser.parse_args(argv)
    return run_all(from_cache=args.from_cache, only=args.only)


if __name__ == "__main__":
    main()
