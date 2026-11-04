"""Recoverability (impairment) blocks for the lab entries IMP-23.

Pure functions on DataFrames. They do not open a file and they do not read the
debtor-name column or the natural-person id column. The one loader at the bottom
opens a read-only connection and runs only when the page is not in synthetic mode.

Aggregates only. A cell with 1 to 4 distinct CNPJs is pooled. The pooled group is
left blank when it still has fewer than 5 CNPJs.
"""

from __future__ import annotations

import json
import math
import os
import warnings
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import polars as pl

FIRST_YEAR = 2016
LAST_YEAR = 2025
MIN_CNPJ = 5
SUPPRESSED = "others (suppressed)"
SEED = 2026
REF_RFB = "2026-03"
MANIFEST_COMMIT = "78f88cd"
GUARANTEE_WARNING = "no guarantee table in the data; collectable share is understated"
CITED_COLLECTION = "1.38%"
CITED_COLLECTABLE = "27.05%"
M1_FORMULA = "paid_flag ~ log_stock + age + rate_l1 + C(year)"
IMP_AGE_BREAKS = (2.0, 5.0, 10.0)
AGE_LABELS = ("(0, 2]", "(2, 5]", "(5, 10]", ">10")
HORIZON_AT_EDGE = {2: "(0, 2]", 5: "(2, 5]", 10: "(5, 10]"}
PARCEL_BREAKS = (1, 12, 60)
PARCEL_LABELS = ("1", "2–12", "13–60", ">60")
WINDOW_START = date(FIRST_YEAR, 1, 1)
WINDOW_END = date(LAST_YEAR, 12, 31)
CENSOR = WINDOW_END
ACTIVE_LABEL = {
    "02": "active",
    "04": "unfit (inapta)",
    "03": "suspended",
    "08": "closed (baixada)",
    "01": "null",
}
PORTE_LABEL = {
    "00": "not informed",
    "01": "micro",
    "03": "small",
    "05": "other sizes",
}
BINARY_OF = {
    "guaranteed": "collectable",
    "uncollectable_suspended": "uncollectable",
    "collectable_instalment": "collectable",
    "unknown_registry": "unknown",
    "collectable_active_history": "collectable",
    "collectable_inactive_history": "collectable",
    "collectable_active_recent": "collectable",
    "uncollectable_inactive_no_history": "uncollectable",
    "uncollectable_active_old": "uncollectable",
    "unclassified": "unknown",
}
SPECIFIC_EN = {
    "guaranteed": "guaranteed",
    "uncollectable_suspended": "uncollectable: suspended",
    "collectable_instalment": "collectable: instalment",
    "unknown_registry": "unknown registry",
    "collectable_active_history": "collectable: active, with payment history",
    "collectable_inactive_history": "collectable: inactive, with payment history",
    "collectable_active_recent": "collectable: active, recent debt, no payment history",
    "uncollectable_inactive_no_history": "uncollectable: inactive, no payment history",
    "uncollectable_active_old": "uncollectable: active, old debt, no payment history",
    "unclassified": "unclassified",
}
_PERSON = {"NOME_DEVEDOR", "CPF_DEVEDOR", "nome_socio", "cpf_cnpj_socio"}
_ID_COL = (
    "id_debito",
    "cnpj",
    "cnpj_root",
    "cpf",
    "nome_devedor",
    "pef",
    "id_solicitacao",
)
PALETTE = ("#154C56", "#BD9869", "#5EA65B", "#8C4A3A", "#8A9499", "#C47B3A")
SQL_INSTALMENTS = """
SELECT ID_SOLICITACAOPARCELAMENTO, DATA_SOLICITACAO, STATUS_PARCELAMENTO,
       DATA_ROMPIMENTO, QTD_PARCELAS, TIPO_PARCELAMENTO, VIGENCIA_PARCELAMENTO,
       REGRA_PARCELAMENTO, ID_DEBITO, DT_INSCRICAO, DT_CONSULTA
FROM SOLICITACAOPARCELAMENTO
"""
SQL_PROTEST = """
SELECT ID_DEBITO, DT_INSCRICAO, STATUS_PROTESTO, DATA_PROTESTO, VALOR_PROTESTADO
FROM STATUS_PROTESTO
"""
FIRM_COLUMNS = (
    "cnpj",
    "match_nivel",
    "cnae_divisao",
    "cnae_secao",
    "situacao_cadastral",
    "porte",
)


@dataclass(frozen=True)
class PgeParams:
    """Defaults for the open points Q1–Q8. Printed on every page."""

    active_code: str = "02"
    inactive_codes: tuple[str, ...] = ("01", "03", "04", "08")
    history_unit: str = "cnpj_root"
    history_scope: str = "vat_icms"
    denominator: str = "enrolled_value"
    history_share: float = 0.10
    history_years: int = 5
    recoverable_years: int = 3
    ref_years: tuple[int, ...] = (2020, 2021, 2022, 2023, 2024)
    recovery_through: int = LAST_YEAR
    precedence: tuple[str, ...] = (
        "suspended",
        "guaranteed",
        "instalment",
        "unknown",
        "active_history",
        "inactive_history",
        "active_recent",
        "inactive_no_history",
        "active_old",
    )
    suspended_token: str = "suspens"
    sample_n: int = 100_000
    seed: int = SEED
    cluster: bool = True
    outcome: str = "paid_flag"


def assumptions_text(params: PgeParams, *, synthetic: bool) -> str:
    """One line per open point. Every threshold is read from ``params``."""
    inactive = ", ".join(params.inactive_codes)
    refs = ", ".join(str(y) for y in params.ref_years)
    bands = ", ".join(AGE_LABELS)
    lines = [
        (
            "Source: synthetic generator. The figures are not the PGE extract."
            if synthetic
            else "Source: VAT (ICMS) legal-entity extract, DuckDB read only."
        ),
        (
            f"Window: {FIRST_YEAR}–{LAST_YEAR}. Rows after {LAST_YEAR} stay out of "
            "every table and every figure."
        ),
        (
            f"Suppression: a cell with 1 to {MIN_CNPJ - 1} distinct CNPJs is pooled "
            f"as {SUPPRESSED!r}. A pool that still has fewer than {MIN_CNPJ} CNPJs "
            "is left blank. The count of pooled cells is printed with each table."
        ),
        (
            f"Q1. Active = RFB code {params.active_code} (ATIVA). Inactive = codes "
            f"{inactive} (nula, suspensa, inapta, baixada). No establishment match, "
            "a root-only match, or any other code = unknown. Unknown is not forced "
            "into collectable or uncollectable."
        ),
        (
            f"Q2. Payment history uses unit {params.history_unit}, scope "
            f"{params.history_scope}, denominator {params.denominator}. "
            f"The share is payments over enrolled value in the {params.history_years} "
            "calendar years that end in the reference year. The threshold is "
            f"{params.history_share:.0%}. The unit's CDAs in the December snapshot "
            "enter the sum. The history is VAT (ICMS) only."
        ),
        (
            f"Q3. Reference dates requested: 31 December of {refs}. A year with no "
            "December snapshot is printed and left out of the tables. December 2024 "
            "is a partial snapshot because of a known gap in the DEBITO extract "
            "(EQ-05). Later recovery is the next calendar year, and the sum from "
            f"the next year through {params.recovery_through}, each divided by the "
            "December stock."
        ),
        (
            "Q4. Precedence: "
            + " → ".join(params.precedence)
            + ". Suspended is uncollectable first. The guaranteed line is always "
            "emitted and is empty. " + GUARANTEE_WARNING + "."
        ),
        (
            f"Q5. Age bands {bands}. A value equal to a break stays in the lower band "
            "(left-closed is false). Ages from 0 to the first break are the first band. "
            "Both readings are shown: the annual paid/stock rate, and the cumulative "
            "recovery of cohorts enrolled in the window. The band above the last break "
            "is not observable on those cohorts."
        ),
        (
            "Q6. The outcome is any payment in the year, the same outcome as model 1 "
            "of the payment section. When the eligible frame is larger than "
            f"{params.sample_n:,} certificate-years, the fit uses a stratified "
            f"sample of that size (year, origin, and the payment outcome), seed "
            f"{params.seed}. Standard errors are HC1. "
            + (
                "Clustered standard errors by CNPJ root are the column beside them."
                if params.cluster
                else "Clustered standard errors are off."
            )
            + " This is an association. It is not a forecast."
        ),
        "Q7. One lab entry per block.",
        (
            f"Q8. The proposal material reports mean collection {CITED_COLLECTION} "
            f"against a collectable share {CITED_COLLECTABLE}. The material states "
            "neither the year nor the tax cut. This page does not reproduce those "
            "two figures. It reports the analogous VAT (ICMS) legal-entity account."
        ),
        (
            f"Registry attributes are the RFB extract {REF_RFB}, which is later than "
            "the debts. CNAE enters as the division (two digits)."
        ),
    ]
    return "\n".join(lines)


def cited_material_note() -> str:
    return (
        "Proposal of variables for the recoverability model (PGE-SP, 2026). "
        "PGE-SP seminar on active debt (2026). "
        f"The seminar material reports mean collection {CITED_COLLECTION} against "
        f"a collectable share {CITED_COLLECTABLE}. Year and tax cut are not stated. "
        "This page does not reproduce those two figures."
    )


def variable_table() -> pl.DataFrame:
    """Availability of the proposal's variables. No query."""
    rows = [
        (
            "CDA number",
            "available",
            "ID_DEBITO, CDA_COMPLETA",
            "Never published. Aggregates only.",
        ),
        (
            "Debtor CPF or CNPJ",
            "partial",
            "CNPJ and CNPJ_ROOT, for the join",
            "The natural-person id is out of scope and is not read.",
        ),
        (
            "Debtor type, natural or legal person",
            "available",
            "Legal-entity filter of extract 1.3",
            "Legal entities only.",
        ),
        ("Enrolment date", "available", "DATA_INSCRICAO", ""),
        ("Date the credit was constituted", "missing", "No column", ""),
        (
            "Nature of the credit",
            "partial",
            "TIPO_DEBITO",
            "The extract keeps VAT (ICMS). Five raw types map to declared, assessed or other.",
        ),
        (
            "Original enrolled value",
            "partial",
            "max of VALOR_SEM_HONORARIOS (max_value)",
            "DEBITO_RECEITA.VL_PRINCIPAL exists and is not used.",
        ),
        (
            "Updated value",
            "available",
            "VALOR_SEM_HONORARIOS in each monthly snapshot (stock)",
            "",
        ),
        (
            "Interest and fines",
            "partial",
            "DEBITO_RECEITA and GARE interest and fine columns",
            "Not used in these entries.",
        ),
        (
            "Consolidated total",
            "partial",
            "VALOR_COM_VH on DEBITO",
            "The lab uses the value net of legal fees.",
        ),
        (
            "Guarantee (flag, type, value, date)",
            "missing",
            "No guarantee table",
            GUARANTEE_WARNING + ".",
        ),
        (
            "Instalment: any, open, counts, break, adhesion date",
            "partial",
            "SOLICITACAOPARCELAMENTO",
            "The status is the status at extraction. It is not a dated status.",
        ),
        (
            "Instalment amount",
            "missing",
            "No amount column on the instalment table",
            "",
        ),
        (
            "Instalments paid or in arrears",
            "missing",
            "Only QTD_PARCELAS, the count on the request",
            "",
        ),
        (
            "Annual gross revenue",
            "partial",
            "Masked turnover, used in one existing section",
            "Not used in these entries.",
        ),
        ("Total assets, equity, employees", "missing", "No column", ""),
        (
            "CNAE or sector",
            "available",
            "RFB registry, division",
            "Division only. The register is later than the debts.",
        ),
        (
            "Size (porte)",
            "partial",
            "porte on the RFB enrichment",
            "Loaded here. Section 3.14 does not load it.",
        ),
        (
            "Natural person: income and wealth",
            "n/a",
            "Out of scope",
            "Legal entities only.",
        ),
        (
            "Debtor's enrolled value and CDA count",
            "partial",
            "Derivable by CNPJ root inside VAT (ICMS)",
            "Other taxes are outside the extract.",
        ),
        (
            "Count of tax foreclosures",
            "partial",
            "AJUIZAMENTO and the filing status on the panel",
            "The filing status is already on the panel. The filing date is not loaded.",
        ),
        (
            "Count of suspended or instalment enrolments",
            "partial",
            "SITUACAO_DEBITO and the instalment table",
            "",
        ),
        (
            "Mean debt per CDA or per economic group",
            "partial",
            "Mean per CDA is derivable by root",
            "The economic group is missing.",
        ),
        (
            "Amount paid in five years, payment count, share of stock",
            "available",
            "GARE and the panel (paid, n_payments, rate)",
            "",
        ),
        (
            "Instalments settled or broken",
            "partial",
            "STATUS_PARCELAMENTO",
            "Extraction status. A settled request has no payoff date.",
        ),
        (
            "Spontaneous payments",
            "missing",
            "GARE does not separate spontaneous, instalment and enforced payments",
            "",
        ),
        (
            "Legal-entity registration status",
            "available",
            "RFB registry, section 3.14",
            "The status is later than the debts.",
        ),
        (
            "Judicial reorganisation or bankruptcy",
            "partial",
            "situacao_especial on the enrichment",
            "Not loaded. Bankruptcy is not verified.",
        ),
        (
            "Natural person alive or deceased",
            "n/a",
            "Out of scope",
            "Legal entities only.",
        ),
        ("Assets: property, vehicles, vessels, aircraft", "missing", "No column", ""),
        ("Equity holdings", "partial", "Partner links are pseudonymised", "Not used."),
        (
            "Protested, and the protest date",
            "partial",
            "Protest table",
            "Loaded here. The protest status is not dated. A null protest date is flagged.",
        ),
        (
            "CADIN, pre-enforcement annotation, credit bureau",
            "missing",
            "No column",
            "",
        ),
        (
            "Foreclosure filed, and the date",
            "partial",
            "Filing status is loaded; the filing date is not",
            "",
        ),
        ("Attachment, SISBAJUD, judicial guarantee", "missing", "No column", ""),
        (
            "Administrative versus judicial phase",
            "partial",
            "Filing status on the panel",
            "",
        ),
        (
            "Secured, stayed or archived execution; statute of limitations",
            "missing",
            "SITUACAO_DEBITO is only a coarse proxy",
            "",
        ),
        ("Age of the CDA", "available", "age on the panel", ""),
        ("Time from constitution to enrolment", "missing", "No constitution date", ""),
        (
            "Time from enrolment to filing",
            "partial",
            "Filing date on AJUIZAMENTO, or the first filed snapshot",
            "Not recomputed here.",
        ),
        (
            "Time from enrolment to the first payment",
            "available",
            "Section 3.14 Kaplan–Meier",
            "",
        ),
        (
            "Time since the last payment",
            "partial",
            "Derivable from GARE",
            "Not computed here.",
        ),
        (
            "Amount recovered, dates, share, recovered yes or no",
            "available",
            "GARE amount, payment date, paid_flag, rate",
            "A payment in the year is not the same as recovery of the credit.",
        ),
        (
            "Recovered within 1, 3, 5 and 10 years",
            "partial",
            "Horizons of 1, 3 and 5 years for cohorts inside the window",
            "Ten years is not observed. The oldest cohort in the window does not reach it.",
        ),
    ]
    return pl.DataFrame(
        {
            "variable": [r[0] for r in rows],
            "status": [r[1] for r in rows],
            "source": [r[2] for r in rows],
            "caveat": [r[3] for r in rows],
        }
    )


def reject_person_columns(df: pl.DataFrame | pl.LazyFrame) -> None:
    cols = df.collect_schema().names() if isinstance(df, pl.LazyFrame) else df.columns
    bad = [c for c in cols if c.upper() in {p.upper() for p in _PERSON}]
    if bad:
        raise ValueError("person columns are not read")


def assert_public(df: pl.DataFrame | pd.DataFrame) -> pl.DataFrame | pd.DataFrame:
    """Refuse a frame that still carries an identifier."""
    cols = list(df.columns)
    bad = [c for c in cols if c.lower() in _ID_COL or "hash" in c.lower()]
    if bad:
        raise ValueError(f"aggregate still has identifier columns: {bad}")
    return df


class PublicTable:
    """Every row and the full labels. A long table scrolls. Identifiers never arrive here."""

    def __init__(self, frame: pd.DataFrame):
        self.frame = frame

    def _repr_html_(self) -> str:
        html = self.frame.to_html(
            index=False,
            border=0,
            escape=True,
            classes="dataframe",
        )
        n = len(self.frame)
        if n <= 25:
            return html
        css = (
            "<style>.lab-tabela-rolavel{max-height:34em;overflow:auto;"
            "border:1px solid #d9d9d9;margin-bottom:.25rem}"
            ".lab-tabela-rolavel thead th{position:sticky;top:0;background:#fff;"
            "z-index:1}</style>"
        )
        return (
            css
            + f"<div class='lab-tabela-rolavel'>{html}</div>"
            + "<p class='lab-sig-legenda' style='font-size:0.8em'>"
            + f"Full table: {n} rows. Scroll inside the box.</p>"
        )

    def __repr__(self) -> str:
        with pd.option_context(
            "display.max_rows",
            None,
            "display.max_columns",
            None,
            "display.max_colwidth",
            None,
            "display.width",
            None,
        ):
            return repr(self.frame)


def publish(df: pl.DataFrame | pd.DataFrame) -> PublicTable:
    """Return a public table for the page. Identifiers raise. Every row is shown."""
    assert_public(df)
    frame = df.to_pandas() if isinstance(df, pl.DataFrame) else df
    return PublicTable(frame.reset_index(drop=True))


def _lazy(df: pl.DataFrame | pl.LazyFrame) -> pl.LazyFrame:
    reject_person_columns(df)
    return df.lazy() if isinstance(df, pl.DataFrame) else df


# Counts real builds, not cache hits. Pages must stay at one certificate index.
_BUILDS = {"cda_index": 0, "ref_facts": 0, "age_base": 0}


def reset_build_counts() -> None:
    for key in _BUILDS:
        _BUILDS[key] = 0


def cda_index_builds() -> int:
    return _BUILDS["cda_index"]


def _collect(lf: pl.LazyFrame) -> pl.DataFrame:
    try:
        return lf.collect(engine="streaming")
    except pl.exceptions.InvalidOperationError:
        return lf.collect()


def _is_lazy(df: pl.DataFrame | pl.LazyFrame) -> bool:
    return isinstance(df, pl.LazyFrame)


# Counts SQL reductions. A page step that stays on the Polars plan does not increment.
_SQL_RUNS = {
    "recovery": 0,
    "share": 0,
    "status": 0,
    "km": 0,
    "break": 0,
    "age": 0,
    "backtest": 0,
    "logit": 0,
    "dates": 0,
}


def sql_runs() -> dict[str, int]:
    """How many heavy steps ran inside DuckDB instead of a Polars collect."""
    return dict(_SQL_RUNS)


def _duck():
    """A spill-to-disk connection. The cap is what keeps a page inside 2 GB extra."""
    import duckdb

    con = duckdb.connect()
    con.execute("SET memory_limit='900MB'")
    con.execute("SET temp_directory='/tmp'")
    con.execute("SET threads=2")
    con.execute("SET preserve_insertion_order=false")
    return con


def _qident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _n_distinct_sql(col: str = "cnpj") -> str:
    """Match Polars ``n_unique``, which counts a null as one extra value."""
    quoted = _qident(col)
    return (
        f"(COUNT(DISTINCT {quoted}) + "
        f"MAX(CASE WHEN {quoted} IS NULL THEN 1 ELSE 0 END))"
    )


def _window_sql(expr: str) -> str:
    start = WINDOW_START.isoformat()
    end = WINDOW_END.isoformat()
    return f"{expr} IS NOT NULL AND {expr} >= DATE '{start}' AND {expr} <= DATE '{end}'"


def _month_sql(start: str, end: str) -> str:
    return f"((year({end}) - year({start})) * 12 + (month({end}) - month({start})))"


def _parcel_case(expr: str) -> str:
    b0, b1, b2 = PARCEL_BREAKS
    labels = tuple(label.replace("'", "''") for label in PARCEL_LABELS)
    return (
        f"CASE WHEN {expr} IS NULL THEN NULL "
        f"WHEN {expr} <= {b0} THEN '{labels[0]}' "
        f"WHEN {expr} <= {b1} THEN '{labels[1]}' "
        f"WHEN {expr} <= {b2} THEN '{labels[2]}' "
        f"ELSE '{labels[3]}' END"
    )


def _age_case(expr: str) -> str:
    b0, b1, b2 = IMP_AGE_BREAKS
    labels = AGE_LABELS
    return (
        f"CASE WHEN {expr} IS NULL OR {expr} < 0 THEN NULL "
        f"WHEN {expr} <= {b0} THEN '{labels[0]}' "
        f"WHEN {expr} <= {b1} THEN '{labels[1]}' "
        f"WHEN {expr} <= {b2} THEN '{labels[2]}' "
        f"ELSE '{labels[3]}' END"
    )


def _status_case() -> str:
    mapped = " ".join(
        f"WHEN CAST(f.registry_code AS VARCHAR) = '{code}' THEN '{label}'"
        for code, label in ACTIVE_LABEL.items()
    )
    nivel = "COALESCE(f.match_nivel, 'nao_encontrado')"
    code = "CAST(f.registry_code AS VARCHAR)"
    mapped_expr = f"CASE {mapped} ELSE NULL END"
    return (
        f"CASE WHEN {nivel} = 'raiz' THEN 'root only (status unknown)' "
        f"WHEN {nivel} <> 'estabelecimento' THEN 'not found in the registry' "
        f"WHEN {mapped_expr} IS NOT NULL THEN {mapped_expr} "
        f"WHEN {code} IS NOT NULL THEN 'other code' "
        f"ELSE 'not found in the registry' END"
    )


def _plan_json(df: pl.LazyFrame) -> dict:
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message=".*serialization format of LazyFrame is deprecated.*",
        )
        raw = df.serialize(format="json")
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode()
    return json.loads(raw)


def _column_names(node) -> list[str]:
    found: list[str] = []

    def walk(value) -> None:
        if isinstance(value, dict):
            column = value.get("Column")
            if isinstance(column, str) and column not in found:
                if column.upper() in {item.upper() for item in _PERSON}:
                    raise ValueError("person columns are not read")
                found.append(column)
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(node)
    return found


def _literal_sql(payload) -> str | None:
    if not isinstance(payload, dict):
        return None
    if "Dyn" in payload:
        dyn = payload["Dyn"]
        if "Int" in dyn:
            return str(int(dyn["Int"]))
        if "Float" in dyn:
            return repr(float(dyn["Float"]))
        if "Boolean" in dyn:
            return "TRUE" if dyn["Boolean"] else "FALSE"
        if "String" in dyn:
            text = str(dyn["String"]).replace("'", "''")
            return f"'{text}'"
    scalar = payload.get("Scalar")
    if isinstance(scalar, dict) and "Datetime" in scalar:
        value, unit, _tz = scalar["Datetime"]
        scale = {
            "Seconds": 1,
            "Milliseconds": 1_000,
            "Microseconds": 1_000_000,
            "Nanoseconds": 1_000_000_000,
        }.get(unit)
        if scale is None:
            return None
        stamp = datetime.fromtimestamp(int(value) / scale, UTC)
        return f"TIMESTAMP '{stamp.strftime('%Y-%m-%d %H:%M:%S')}'"
    return None


def _expr_sql(node) -> str | None:
    """A SQL expression for a filter or a projected column. None if unsupported."""
    if not isinstance(node, dict):
        return None
    if isinstance(node.get("Column"), str):
        name = node["Column"]
        if name.upper() in {item.upper() for item in _PERSON}:
            raise ValueError("person columns are not read")
        return _qident(name)
    if "Alias" in node:
        inner = node["Alias"][0]
        return _expr_sql(inner)
    if "Cast" in node:
        inner = node["Cast"].get("expr")
        if isinstance(inner, dict) and (
            "Literal" in inner or "Alias" in inner or "Cast" in inner
        ):
            return _expr_sql(inner)
        return None
    if "BinaryExpr" in node:
        body = node["BinaryExpr"]
        op = {
            "LtEq": "<=",
            "GtEq": ">=",
            "Lt": "<",
            "Gt": ">",
            "Eq": "=",
            "NotEq": "<>",
            "And": "AND",
            "Or": "OR",
        }.get(body.get("op"))
        if op is None:
            return None
        left = _expr_sql(body.get("left"))
        right = _expr_sql(body.get("right"))
        if left is None or right is None:
            return None
        return f"({left} {op} {right})"
    if "Literal" in node:
        return _literal_sql(node["Literal"])
    return None


def _projection(expr) -> tuple[str, str] | None:
    if isinstance(expr, dict) and isinstance(expr.get("Column"), str):
        name = expr["Column"]
        if name.upper() in {item.upper() for item in _PERSON}:
            raise ValueError("person columns are not read")
        return _qident(name), name
    if isinstance(expr, dict) and "Alias" in expr:
        inner, name = expr["Alias"]
        sql = _expr_sql(inner)
        if sql is None or not isinstance(name, str):
            return None
        return sql, name
    return None


def _compile_scan(scan: dict) -> str | None:
    paths = []
    for item in scan.get("sources", {}).get("Paths", []):
        path = item.get("inner") if isinstance(item, dict) else None
        if not path:
            return None
        paths.append(path.replace("'", "''"))
    if not paths:
        return None
    hive = bool(
        scan.get("unified_scan_args", {}).get("hive_options", {}).get("enabled")
    )
    quoted = ", ".join(f"'{path}'" for path in paths)
    if hive:
        return f"read_parquet([{quoted}], hive_partitioning=true)"
    return f"read_parquet([{quoted}])"


def _compile_select(sel: dict) -> str | None:
    projections = []
    for expr in sel.get("expr", []):
        projected = _projection(expr)
        if projected is None:
            return None
        projections.append(projected)
    if not projections:
        return None
    inner_node = sel.get("input")
    if isinstance(inner_node, dict) and "Filter" in inner_node:
        filt = inner_node["Filter"]
        predicate = _expr_sql(filt.get("predicate"))
        source = _compile_node(filt.get("input"))
        if predicate is None or source is None:
            return None
        needed: list[str] = []
        for expr in sel.get("expr", []):
            for column in _column_names(expr):
                if column not in needed:
                    needed.append(column)
        for column in _column_names(filt.get("predicate")):
            if column not in needed:
                needed.append(column)
        cols = ", ".join(_qident(column) for column in needed)
        inner = f"(SELECT {cols} FROM {source} WHERE {predicate})"
    else:
        inner = _compile_node(inner_node)
        if inner is None:
            return None
    parts = [f"{sql} AS {_qident(name)}" for sql, name in projections]
    return "(SELECT " + ", ".join(parts) + f" FROM {inner})"


def _compile_node(node) -> str | None:
    if not isinstance(node, dict):
        return None
    if "Select" in node:
        return _compile_select(node["Select"])
    if "Scan" in node:
        return _compile_scan(node["Scan"])
    if "Filter" in node:
        filt = node["Filter"]
        predicate = _expr_sql(filt.get("predicate"))
        source = _compile_node(filt.get("input"))
        if predicate is None or source is None:
            return None
        needed = _column_names(filt.get("predicate"))
        cols = ", ".join(_qident(column) for column in needed)
        return f"(SELECT {cols} FROM {source} WHERE {predicate})"
    return None


def _unwrap_plan(plan: dict) -> dict:
    """Polars wraps some plans as ``{"IR": {"dsl": ...}}`` and leaves others bare."""
    if "IR" in plan and isinstance(plan["IR"], dict):
        inner = plan["IR"]
        if "dsl" in inner and isinstance(inner["dsl"], dict):
            return inner["dsl"]
        return inner
    return plan


def _compile_relation(df: pl.LazyFrame) -> str | None:
    """SQL for a scan, a rename, and a simple filter. None when the plan joins."""
    try:
        return _compile_node(_unwrap_plan(_plan_json(df)))
    except ValueError:
        raise
    except (AttributeError, KeyError, TypeError, json.JSONDecodeError):
        return None


def _sql_relation(con, df: pl.DataFrame | pl.LazyFrame, view: str) -> str | None:
    """A SQL relation. A DataFrame is registered. A scan stays on disk."""
    if isinstance(df, pl.DataFrame):
        con.register(view, df)
        return view
    if isinstance(df, pl.LazyFrame):
        return _compile_relation(df)
    return None


def _acquire_relation(df: pl.LazyFrame) -> tuple[str, list[str]]:
    """SQL for ``df``. A joined plan is spilled once, then read back with a cap."""
    sql = _compile_relation(df)
    if sql is not None:
        return sql, []
    import tempfile

    handle, name = tempfile.mkstemp(prefix="imp23-", suffix=".parquet")
    os.close(handle)
    df.sink_parquet(name, compression="zstd")
    quoted = name.replace("'", "''")
    return f"read_parquet('{quoted}')", [name]


def _drop_files(paths: list[str]) -> None:
    for path in paths:
        if path and os.path.exists(path):
            os.unlink(path)


def _suppress_sql(
    con,
    table: str,
    group_cols: list[str],
    metrics: dict[str, str],
    *,
    pool: str,
) -> tuple[pl.DataFrame, int]:
    """Pool 1–4 CNPJ cells from a DuckDB relation. Detail rows are not collected."""
    keys = list(dict.fromkeys([*group_cols, pool]))
    key_sql = ", ".join(_qident(key) for key in keys)
    metric_sql = ", ".join(
        f"{expr} AS {_qident(name)}" for name, expr in metrics.items()
    )
    nsql = _n_distinct_sql()
    first = con.execute(
        f"""
        SELECT {key_sql}, {metric_sql}, {nsql} AS {_qident("n_cnpj")}
        FROM {table}
        GROUP BY {key_sql}
        """
    ).pl()
    small = first.filter((pl.col("n_cnpj") > 0) & (pl.col("n_cnpj") < MIN_CNPJ))
    n_sup = small.height
    if n_sup == 0:
        return first.sort(keys), 0
    con.register("_small_cells", small.select(keys))
    join_on = " AND ".join(f"b.{_qident(key)} = s.{_qident(key)}" for key in keys)
    label = "'" + SUPPRESSED.replace("'", "''") + "'"
    grouped = []
    for key in keys:
        if key == pool:
            grouped.append(
                f"CASE WHEN s.{_qident(key)} IS NOT NULL THEN {label} "
                f"ELSE b.{_qident(key)} END AS {_qident(key)}"
            )
        else:
            grouped.append(f"b.{_qident(key)} AS {_qident(key)}")
    group_by = ", ".join(str(i) for i in range(1, len(keys) + 1))
    out = con.execute(
        f"""
        SELECT {", ".join(grouped)}, {metric_sql}, {nsql} AS {_qident("n_cnpj")}
        FROM {table} b
        LEFT JOIN _small_cells s ON {join_on}
        GROUP BY {group_by}
        """
    ).pl()
    value_cols = [col for col in out.columns if col not in keys]
    out = out.with_columns(
        [
            pl.when((pl.col(pool) == SUPPRESSED) & (pl.col("n_cnpj") < MIN_CNPJ))
            .then(pl.lit(None))
            .otherwise(pl.col(col))
            .alias(col)
            for col in value_cols
        ]
    )
    return out.sort(keys), n_sup


def age_band_expr(col: str = "age") -> pl.Expr:
    """Bands with the lab's left-closed-false rule. Breaks are the right edges."""
    age = pl.col(col)
    b0, b1, b2 = IMP_AGE_BREAKS
    return (
        pl.when(age.is_null() | (age < 0))
        .then(pl.lit(None))
        .when(age <= b0)
        .then(pl.lit(AGE_LABELS[0]))
        .when(age <= b1)
        .then(pl.lit(AGE_LABELS[1]))
        .when(age <= b2)
        .then(pl.lit(AGE_LABELS[2]))
        .otherwise(pl.lit(AGE_LABELS[3]))
        .alias("age_band")
    )


def parcel_band_expr(col: str = "qtd_parcelas") -> pl.Expr:
    n = pl.col(col)
    b0, b1, b2 = PARCEL_BREAKS
    return (
        pl.when(n.is_null())
        .then(pl.lit(None))
        .when(n <= b0)
        .then(pl.lit(PARCEL_LABELS[0]))
        .when(n <= b1)
        .then(pl.lit(PARCEL_LABELS[1]))
        .when(n <= b2)
        .then(pl.lit(PARCEL_LABELS[2]))
        .otherwise(pl.lit(PARCEL_LABELS[3]))
        .alias("parcel_band")
    )


def date_quality(df: pl.DataFrame | pl.LazyFrame, col: str) -> dict[str, Any]:
    """Count impossible dates in one pass. Do not rewrite them."""
    reject_person_columns(df)
    if _is_lazy(df):
        counted = _date_quality_sql(df, col)
        if counted is not None:
            return counted
    stats = _collect(
        _lazy(df).select(
            pl.len().alias("n"),
            pl.col(col).null_count().alias("n_null"),
            (pl.col(col).is_not_null() & (pl.col(col) < WINDOW_START))
            .sum()
            .alias("n_before_window"),
            (pl.col(col).is_not_null() & (pl.col(col) > WINDOW_END))
            .sum()
            .alias("n_after_window"),
            (pl.col(col).is_not_null() & (pl.col(col).dt.year() < 1000))
            .sum()
            .alias("n_year_before_1000"),
        )
    )
    row = stats.row(0, named=True)
    return {
        "column": col,
        **{key: int(row[key] or 0) for key in row if key != "column"},
    }


def _plausible(col: str) -> pl.Expr:
    c = pl.col(col)
    return c.is_not_null() & (c >= WINDOW_START) & (c <= WINDOW_END)


def _safe_exp(value: float) -> float:
    if not math.isfinite(value):
        return float("nan")
    if value > 50:
        return float("inf")
    if value < -50:
        return 0.0
    return math.exp(value)


def stars(p: float | None) -> str:
    try:
        v = float(p)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return ""
    if math.isnan(v):
        return ""
    if v <= 0.01:
        return "***"
    if v <= 0.05:
        return "**"
    if v <= 0.10:
        return "*"
    return ""


def suppress_aggregate(
    df: pl.DataFrame | pl.LazyFrame,
    group_cols: list[str],
    metrics: dict[str, pl.Expr],
    *,
    pool: str,
    whole: bool = False,
    cnpj_col: str = "cnpj",
    min_n: int = MIN_CNPJ,
) -> tuple[pl.DataFrame, int]:
    """Pool small cells, re-aggregate from the microdata, blank a pool still under the floor.

    ``whole`` pools every row of a label when any of its cells is small (the CNAE rule).
    Otherwise only the small cells change label.
    """
    lf = _lazy(df)
    keys = list(dict.fromkeys([*group_cols, pool]))
    metric_aggs = [expr.alias(name) for name, expr in metrics.items()]
    metric_aggs.append(pl.col(cnpj_col).n_unique().alias("n_cnpj"))
    # One pass when every cell is large enough to publish. A small cell needs a
    # second pass so pooled CNPJs are counted once.
    first = _collect(lf.group_by(keys).agg(metric_aggs))
    small = first.filter((pl.col("n_cnpj") > 0) & (pl.col("n_cnpj") < min_n))
    n_suppressed = small.height
    if n_suppressed == 0:
        return first.sort(keys), 0
    if whole:
        labels = small[pool].unique().to_list()
        marked = lf.with_columns(
            pl.when(pl.col(pool).is_in(labels))
            .then(pl.lit(SUPPRESSED))
            .otherwise(pl.col(pool))
            .alias(pool)
        )
    elif small.height:
        flag = small.select(keys).with_columns(pl.lit(True).alias("_small"))
        marked = lf.join(flag.lazy(), on=keys, how="left").with_columns(
            pl.when(pl.col("_small").fill_null(False))
            .then(pl.lit(SUPPRESSED))
            .otherwise(pl.col(pool))
            .alias(pool)
        )
    else:
        marked = lf
    out = _collect(marked.group_by(keys).agg(metric_aggs))
    value_cols = [c for c in out.columns if c not in keys]
    out = out.with_columns(
        [
            pl.when((pl.col(pool) == SUPPRESSED) & (pl.col("n_cnpj") < min_n))
            .then(pl.lit(None))
            .otherwise(pl.col(c))
            .alias(c)
            for c in value_cols
        ]
    )
    return out.sort(keys), n_suppressed


def _unit_expr(params: PgeParams) -> pl.Expr:
    if params.history_unit == "cnpj":
        return pl.col("cnpj").alias("unit_key")
    if params.history_unit == "cda":
        return pl.col("id_debito").cast(pl.Utf8).alias("unit_key")
    if params.history_unit == "cnpj_root":
        return pl.col("cnpj_root").alias("unit_key")
    raise ValueError(f"unknown history unit: {params.history_unit}")


def _activity_expr(params: PgeParams) -> pl.Expr:
    code = pl.col("registry_code").cast(pl.Utf8)
    nivel = pl.col("match_nivel").fill_null("nao_encontrado")
    inactive = list(params.inactive_codes)
    return (
        pl.when(nivel != "estabelecimento")
        .then(pl.lit("unknown"))
        .when(code == params.active_code)
        .then(pl.lit("active"))
        .when(code.is_in(inactive))
        .then(pl.lit("inactive"))
        .otherwise(pl.lit("unknown"))
        .alias("activity")
    )


def _status_expr() -> pl.Expr:
    code = pl.col("registry_code").cast(pl.Utf8)
    nivel = pl.col("match_nivel").fill_null("nao_encontrado")
    mapped = (
        pl.col("registry_code").cast(pl.Utf8).replace_strict(ACTIVE_LABEL, default=None)
    )
    return (
        pl.when(nivel == "raiz")
        .then(pl.lit("root only (status unknown)"))
        .when(nivel != "estabelecimento")
        .then(pl.lit("not found in the registry"))
        .when(mapped.is_not_null())
        .then(mapped)
        .when(code.is_not_null())
        .then(pl.lit("other code"))
        .otherwise(pl.lit("not found in the registry"))
        .alias("status")
    )


def _years_before(d: date, n: int) -> date:
    return date(d.year - n, d.month, d.day)


def classify_pge(df: pl.DataFrame, ref_date: date, params: PgeParams) -> pl.DataFrame:
    """Classify each in-stock CDA. History is summed on the rows that are passed in.

    Rows with ``in_stock`` false contribute to the unit's history and are dropped
    from the result. A ``person_type`` other than PJ is dropped and counted.
    """
    reject_person_columns(df)
    work = df
    if "person_type" in work.columns:
        work = work.filter(pl.col("person_type").fill_null("PJ") == "PJ")
    if "in_stock" not in work.columns:
        work = work.with_columns(pl.lit(True).alias("in_stock"))
    if "guaranteed" not in work.columns:
        work = work.with_columns(pl.lit(False).alias("guaranteed"))
    if "instalment_open" not in work.columns:
        work = work.with_columns(pl.lit(False).alias("instalment_open"))
    denom = params.denominator
    work = work.with_columns(_unit_expr(params), _activity_expr(params))
    hist = (
        work.group_by("unit_key")
        .agg(
            pl.col("paid_5y").fill_null(0).sum().alias("_paid5"),
            pl.col(denom).fill_null(0).sum().alias("_den"),
            pl.col("paid_3y").fill_null(0).sum().alias("_paid3"),
        )
        .with_columns(
            pl.when(pl.col("_den") > 0)
            .then(pl.col("_paid5") / pl.col("_den"))
            .otherwise(pl.lit(0.0))
            .alias("history_share"),
            (pl.col("_paid3") > 0).alias("collected_3y"),
        )
    )
    work = work.join(hist, on="unit_key", how="left")
    recent_cut = _years_before(ref_date, params.history_years)
    recent = pl.col("enrol_date").is_not_null() & (pl.col("enrol_date") >= recent_cut)
    suspended = (
        pl.col("situacao")
        .fill_null("")
        .str.to_lowercase()
        .str.contains(params.suspended_token)
    )
    has_hist = pl.col("history_share").fill_null(0) >= params.history_share
    active = pl.col("activity") == "active"
    inactive = pl.col("activity") == "inactive"
    unknown = pl.col("activity") == "unknown"
    # The tuple is the order. Each branch is applied only if it is in the tuple,
    # and earlier branches win.
    branches: dict[str, pl.Expr] = {
        "suspended": suspended,
        "guaranteed": pl.col("guaranteed").fill_null(False),
        "instalment": pl.col("instalment_open").fill_null(False),
        "unknown": unknown,
        "active_history": active & has_hist,
        "inactive_history": inactive & has_hist,
        "active_recent": active & ~has_hist & recent,
        "inactive_no_history": inactive & ~has_hist,
        "active_old": active & ~has_hist & ~recent,
    }
    names = {
        "suspended": "uncollectable_suspended",
        "guaranteed": "guaranteed",
        "instalment": "collectable_instalment",
        "unknown": "unknown_registry",
        "active_history": "collectable_active_history",
        "inactive_history": "collectable_inactive_history",
        "active_recent": "collectable_active_recent",
        "inactive_no_history": "uncollectable_inactive_no_history",
        "active_old": "uncollectable_active_old",
    }
    expr: pl.Expr = pl.lit("unclassified")
    for key in reversed(params.precedence):
        if key not in branches:
            raise ValueError(f"unknown precedence key: {key}")
        expr = pl.when(branches[key]).then(pl.lit(names[key])).otherwise(expr)
    classified = work.with_columns(expr.alias("specific")).with_columns(
        pl.col("specific")
        .replace_strict(BINARY_OF, default="unknown")
        .alias("binary_class"),
        (
            pl.col("specific")
            .replace_strict(BINARY_OF, default="unknown")
            .eq("collectable")
            & pl.col("collected_3y").fill_null(False)
        ).alias("recoverable"),
    )
    return classified.filter(pl.col("in_stock"))


def summarise_pge(
    classified: pl.DataFrame, ref_year: int, *, min_n: int = MIN_CNPJ
) -> tuple[pl.DataFrame, int]:
    """Origin × class aggregates, plus an empty guaranteed row."""
    frame = classified.with_columns(
        pl.col("specific")
        .replace_strict(SPECIFIC_EN, default=pl.col("specific"))
        .alias("class_label")
    )
    metrics = {
        "n_cda": pl.col("id_debito").n_unique(),
        "stock": pl.col("stock").sum(),
        "recoverable_cda": pl.col("id_debito").filter(pl.col("recoverable")).n_unique(),
        "recoverable_stock": pl.col("stock").filter(pl.col("recoverable")).sum(),
    }
    out, n_sup = suppress_aggregate(
        frame,
        ["origin", "binary_class"],
        metrics,
        pool="class_label",
        min_n=min_n,
    )
    out = out.with_columns(
        (pl.col("stock") / pl.col("stock").sum().over("origin")).alias("stock_share"),
        pl.lit(ref_year).alias("ref_year"),
        pl.lit(None).cast(pl.Utf8).alias("note"),
    )
    origins = out["origin"].unique().to_list() or ["declared"]
    empty = pl.DataFrame(
        {
            "origin": origins,
            "binary_class": ["collectable"] * len(origins),
            "class_label": ["guaranteed"] * len(origins),
            "n_cda": [0] * len(origins),
            "stock": [0.0] * len(origins),
            "recoverable_cda": [0] * len(origins),
            "recoverable_stock": [0.0] * len(origins),
            "n_cnpj": [0] * len(origins),
            "stock_share": [0.0] * len(origins),
            "ref_year": [ref_year] * len(origins),
            "note": [GUARANTEE_WARNING] * len(origins),
        }
    )
    # A real guaranteed row (only a unit test sets the flag) keeps its counts.
    # The structural empty row is added only when the class is absent.
    have = set(out.filter(pl.col("class_label") == "guaranteed")["origin"].to_list())
    empty = empty.filter(~pl.col("origin").is_in(list(have)))
    if empty.height and not have:
        # Attach the warning on a single structural row per origin.
        pass
    elif have:
        out = out.with_columns(
            pl.when(pl.col("class_label") == "guaranteed")
            .then(pl.lit(GUARANTEE_WARNING))
            .otherwise(pl.col("note"))
            .alias("note")
        )
    published = assert_public(pl.concat([out, empty], how="diagonal_relaxed"))
    return published, n_sup


def _paid_in_years(
    panel: pl.DataFrame | pl.LazyFrame, y0: int, y1: int
) -> pl.DataFrame:
    y1 = min(y1, LAST_YEAR)
    return _collect(
        _lazy(panel)
        .filter((pl.col("year") >= y0) & (pl.col("year") <= y1))
        .group_by("id_debito")
        .agg(pl.col("paid").fill_null(0).sum().alias("paid_window"))
    )


def instalment_open_ids(instalments: pl.DataFrame, ref: date) -> pl.DataFrame:
    """A request is open when its date is on or before ``ref`` and no valid break is."""
    reject_person_columns(instalments)
    if instalments.height == 0:
        return pl.DataFrame({"id_debito": [], "instalment_open": []}).cast(
            {"id_debito": pl.Int64, "instalment_open": pl.Boolean}
        )
    req = instalments.filter(
        _plausible("data_solicitacao") & (pl.col("data_solicitacao") <= ref)
    )
    broken = _plausible("data_rompimento") & (pl.col("data_rompimento") <= ref)
    req = req.with_columns((~broken).alias("still_open"))
    if req.height == 0:
        return pl.DataFrame(
            {
                "id_debito": pl.Series([], dtype=pl.Int64),
                "instalment_open": pl.Series([], dtype=pl.Boolean),
            }
        )
    return req.group_by("id_debito").agg(
        pl.col("still_open").any().alias("instalment_open")
    )


def cda_at_ref(
    panel: pl.DataFrame | pl.LazyFrame,
    firms: pl.DataFrame,
    instalments: pl.DataFrame,
    ref_year: int,
    params: PgeParams,
) -> pl.DataFrame:
    """December snapshot of ``ref_year`` with the inputs of ``classify_pge``."""
    reject_person_columns(firms)
    ref = date(ref_year, 12, 31)
    snap = _collect(
        _lazy(panel)
        .filter(
            (pl.col("year") == ref_year)
            & (pl.col("year") <= LAST_YEAR)
            & pl.col("present_in_december").fill_null(False)
            & (pl.col("stock") > 0)
        )
        .group_by("id_debito")
        .agg(
            pl.col("cnpj").drop_nulls().first(),
            pl.col("cnpj_root").drop_nulls().first(),
            pl.col("origin").drop_nulls().first(),
            pl.col("enrol_date").min(),
            pl.col("situacao").drop_nulls().first(),
            pl.col("stock").sum(),
            pl.col("enrolled_value").max(),
        )
    )
    y0 = ref_year - params.history_years + 1
    y3 = ref_year - params.recoverable_years + 1
    paid5 = _paid_in_years(panel, y0, ref_year).rename({"paid_window": "paid_5y"})
    paid3 = _paid_in_years(panel, y3, ref_year).rename({"paid_window": "paid_3y"})
    firm = firms.select(
        pl.col("cnpj"),
        pl.col("match_nivel"),
        pl.col("situacao_cadastral").alias("registry_code"),
    )
    open_ids = instalment_open_ids(instalments, ref)
    out = (
        snap.join(paid5, on="id_debito", how="left")
        .join(paid3, on="id_debito", how="left")
        .join(firm, on="cnpj", how="left")
        .join(open_ids, on="id_debito", how="left")
        .with_columns(
            pl.col("paid_5y").fill_null(0),
            pl.col("paid_3y").fill_null(0),
            pl.col("instalment_open").fill_null(False),
            pl.col("match_nivel").fill_null("nao_encontrado"),
            pl.lit(False).alias("guaranteed"),
            pl.lit(True).alias("in_stock"),
        )
    )
    return out


def recovery_by_class(
    panel: pl.DataFrame | pl.LazyFrame,
    classified: pl.DataFrame,
    ref_year: int,
    *,
    through: int = LAST_YEAR,
) -> tuple[pl.DataFrame, int]:
    """Next-year and accumulated recovery of the December stock, by class."""
    later_end = min(through, LAST_YEAR)
    later = _collect(
        _lazy(panel)
        .filter((pl.col("year") >= ref_year + 1) & (pl.col("year") <= later_end))
        .group_by("id_debito")
        .agg(pl.col("paid").sum().alias("paid_after"))
    )
    nxt = _collect(
        _lazy(panel)
        .filter(pl.col("year") == ref_year + 1)
        .group_by("id_debito")
        .agg(pl.col("paid").sum().alias("paid_next"))
    )
    frame = (
        classified.select("id_debito", "cnpj", "origin", "binary_class", "stock")
        .join(later, on="id_debito", how="left")
        .join(nxt, on="id_debito", how="left")
        .with_columns(
            pl.col("paid_after").fill_null(0), pl.col("paid_next").fill_null(0)
        )
    )
    out, n_sup = suppress_aggregate(
        frame,
        ["origin"],
        {
            "n_cda": pl.col("id_debito").n_unique(),
            "stock": pl.col("stock").sum(),
            "paid_next": pl.col("paid_next").sum(),
            "paid_after": pl.col("paid_after").sum(),
        },
        pool="binary_class",
    )
    out = out.with_columns(
        (pl.col("paid_next") / pl.col("stock")).alias("recovery_next"),
        (pl.col("paid_after") / pl.col("stock")).alias("recovery_to_end"),
        pl.lit(ref_year).alias("ref_year"),
    )
    return assert_public(out), n_sup


def _ref_facts_spill(
    panel: pl.LazyFrame, years: list[int], params: PgeParams
) -> pl.DataFrame:
    lf = panel.filter(
        (pl.col("year") >= FIRST_YEAR) & (pl.col("year") <= LAST_YEAR)
    ).select(
        "id_debito",
        "year",
        "cnpj",
        "cnpj_root",
        "origin",
        "enrol_date",
        "situacao",
        "stock",
        "paid",
        "present_in_december",
        "enrolled_value",
    )
    relation, temps = _acquire_relation(lf)
    con = _duck()
    try:
        select = ["id_debito"]
        for year in years:
            snap = f"year = {year} AND present_in_december AND stock > 0"
            y0 = year - params.history_years + 1
            y3 = year - params.recoverable_years + 1
            later = min(params.recovery_through, LAST_YEAR)
            select.extend(
                [
                    f"SUM(CASE WHEN {snap} THEN 1 ELSE 0 END) AS n_snap_{year}",
                    f"MAX(CASE WHEN {snap} THEN cnpj END) AS cnpj_{year}",
                    f"MAX(CASE WHEN {snap} THEN cnpj_root END) AS root_{year}",
                    f"MAX(CASE WHEN {snap} THEN origin END) AS origin_{year}",
                    f"MIN(CASE WHEN {snap} THEN enrol_date END) AS enrol_{year}",
                    f"MAX(CASE WHEN {snap} THEN situacao END) AS situacao_{year}",
                    f"COALESCE(SUM(CASE WHEN {snap} THEN stock END), 0) AS stock_{year}",
                    f"MAX(CASE WHEN {snap} THEN enrolled_value END) AS enrolled_{year}",
                    f"SUM(CASE WHEN year BETWEEN {y0} AND {year} THEN COALESCE(paid, 0) ELSE 0 END) AS paid5_{year}",
                    f"SUM(CASE WHEN year BETWEEN {y3} AND {year} THEN COALESCE(paid, 0) ELSE 0 END) AS paid3_{year}",
                    f"SUM(CASE WHEN year = {year + 1} THEN COALESCE(paid, 0) ELSE 0 END) AS next_{year}",
                    f"SUM(CASE WHEN year BETWEEN {year + 1} AND {later} THEN COALESCE(paid, 0) ELSE 0 END) AS after_{year}",
                ]
            )
        out = con.execute(
            f"SELECT {', '.join(select)} FROM {relation} GROUP BY id_debito"
        ).pl()
        return out
    finally:
        con.close()
        _drop_files(temps)


def ref_facts(panel: pl.DataFrame | pl.LazyFrame, params: PgeParams) -> pl.DataFrame:
    """One row per certificate with every reference-year stock and payment window.

    Replaces one panel scan per year and per payment window.
    """
    _BUILDS["ref_facts"] += 1
    years = [year for year in params.ref_years if year <= LAST_YEAR]
    if isinstance(panel, pl.LazyFrame):
        # Spill the narrow projection and aggregate under a memory cap, so a
        # 20-million-row panel does not have to sit in an 8 GB machine.
        return _ref_facts_spill(panel, years, params)
    lf = (
        _lazy(panel)
        .filter((pl.col("year") >= FIRST_YEAR) & (pl.col("year") <= LAST_YEAR))
        .select(
            "id_debito",
            "year",
            "cnpj",
            "cnpj_root",
            "origin",
            "enrol_date",
            "situacao",
            "stock",
            "paid",
            "present_in_december",
            "enrolled_value",
        )
    )
    aggs: list[pl.Expr] = []
    for year in years:
        in_snap = (
            (pl.col("year") == year)
            & pl.col("present_in_december").fill_null(False)
            & (pl.col("stock") > 0)
        )
        y0 = year - params.history_years + 1
        y3 = year - params.recoverable_years + 1
        later_end = min(params.recovery_through, LAST_YEAR)
        paid = pl.col("paid").fill_null(0)
        aggs.extend(
            [
                in_snap.sum().alias(f"n_snap_{year}"),
                pl.col("cnpj")
                .filter(in_snap)
                .drop_nulls()
                .first()
                .alias(f"cnpj_{year}"),
                pl.col("cnpj_root")
                .filter(in_snap)
                .drop_nulls()
                .first()
                .alias(f"root_{year}"),
                pl.col("origin")
                .filter(in_snap)
                .drop_nulls()
                .first()
                .alias(f"origin_{year}"),
                pl.col("enrol_date").filter(in_snap).min().alias(f"enrol_{year}"),
                pl.col("situacao")
                .filter(in_snap)
                .drop_nulls()
                .first()
                .alias(f"situacao_{year}"),
                pl.col("stock")
                .filter(in_snap)
                .sum()
                .fill_null(0)
                .alias(f"stock_{year}"),
                pl.col("enrolled_value")
                .filter(in_snap)
                .max()
                .alias(f"enrolled_{year}"),
                paid.filter((pl.col("year") >= y0) & (pl.col("year") <= year))
                .sum()
                .fill_null(0)
                .alias(f"paid5_{year}"),
                paid.filter((pl.col("year") >= y3) & (pl.col("year") <= year))
                .sum()
                .fill_null(0)
                .alias(f"paid3_{year}"),
                paid.filter(pl.col("year") == year + 1)
                .sum()
                .fill_null(0)
                .alias(f"next_{year}"),
                paid.filter(
                    (pl.col("year") >= year + 1) & (pl.col("year") <= later_end)
                )
                .sum()
                .fill_null(0)
                .alias(f"after_{year}"),
            ]
        )
    if not aggs:
        return pl.DataFrame({"id_debito": pl.Series([], dtype=pl.Int64)})
    return _collect(lf.group_by("id_debito").agg(aggs))


def _open_flags(
    instalments: pl.DataFrame | pl.LazyFrame, years: list[int]
) -> pl.DataFrame:
    """Open-instalment flag at each reference date, in one pass over the requests."""
    aggs = []
    for year in years:
        ref = pl.lit(date(year, 12, 31))
        requested = _plausible("data_solicitacao") & (pl.col("data_solicitacao") <= ref)
        broken = _plausible("data_rompimento") & (pl.col("data_rompimento") <= ref)
        aggs.append((requested & ~broken).any().fill_null(False).alias(f"open_{year}"))
    if not aggs:
        return pl.DataFrame({"id_debito": pl.Series([], dtype=pl.Int64)})
    return _collect(_lazy(instalments).group_by("id_debito").agg(aggs))


def _recovery_prepared(
    classified: pl.DataFrame, ref_year: int
) -> tuple[pl.DataFrame, int]:
    frame = classified.select(
        "id_debito",
        "cnpj",
        "origin",
        "binary_class",
        "stock",
        "paid_next",
        "paid_after",
    )
    out, n_sup = suppress_aggregate(
        frame,
        ["origin"],
        {
            "n_cda": pl.col("id_debito").n_unique(),
            "stock": pl.col("stock").sum(),
            "paid_next": pl.col("paid_next").sum(),
            "paid_after": pl.col("paid_after").sum(),
        },
        pool="binary_class",
    )
    out = out.with_columns(
        (pl.col("paid_next") / pl.col("stock")).alias("recovery_next"),
        (pl.col("paid_after") / pl.col("stock")).alias("recovery_to_end"),
        pl.lit(ref_year).alias("ref_year"),
    )
    return assert_public(out), n_sup


_FACT_COLUMNS = (
    "id_debito",
    "year",
    "cnpj",
    "cnpj_root",
    "origin",
    "enrol_date",
    "situacao",
    "stock",
    "paid",
    "present_in_december",
    "enrolled_value",
)


def _store_open_flags(con, instalments, years: list[int]) -> str | None:
    """Open-instalment flags stay in DuckDB. They are not collected."""
    relation = _sql_relation(con, instalments, "instalments")
    if relation is None:
        return None
    flags = []
    for year in years:
        ref = date(year, 12, 31).isoformat()
        requested = (
            f"({_window_sql('data_solicitacao')} AND data_solicitacao <= DATE '{ref}')"
        )
        broken = (
            f"({_window_sql('data_rompimento')} AND data_rompimento <= DATE '{ref}')"
        )
        flags.append(
            f"BOOL_OR({requested} AND NOT ({broken})) AS {_qident(f'open_{year}')}"
        )
    if not flags:
        return None
    con.execute(
        f"""
        CREATE TEMP TABLE opens AS
        SELECT CAST(id_debito AS BIGINT) AS id_debito, {", ".join(flags)}
        FROM {relation}
        GROUP BY 1
        """
    )
    return "opens"


def _store_year_snap(con, relation: str, year: int, params: PgeParams) -> None:
    """One row per in-stock debt for one reference year. The rows stay in DuckDB."""
    y0 = year - params.history_years + 1
    y3 = year - params.recoverable_years + 1
    later = min(params.recovery_through, LAST_YEAR)
    snap = f"year = {year} AND COALESCE(present_in_december, FALSE) AND stock > 0"
    con.execute("DROP TABLE IF EXISTS year_snap")
    con.execute(
        f"""
        CREATE TEMP TABLE year_snap AS
        SELECT id_debito,
               MAX(CASE WHEN {snap} THEN cnpj END) AS cnpj,
               MAX(CASE WHEN {snap} THEN cnpj_root END) AS cnpj_root,
               MAX(CASE WHEN {snap} THEN origin END) AS origin,
               MIN(CASE WHEN {snap} THEN enrol_date END) AS enrol_date,
               MAX(CASE WHEN {snap} THEN situacao END) AS situacao,
               COALESCE(SUM(CASE WHEN {snap} THEN stock END), 0) AS stock,
               MAX(CASE WHEN {snap} THEN enrolled_value END) AS enrolled_value,
               SUM(CASE WHEN year BETWEEN {y0} AND {year}
                        THEN COALESCE(paid, 0) ELSE 0 END) AS paid_5y,
               SUM(CASE WHEN year BETWEEN {y3} AND {year}
                        THEN COALESCE(paid, 0) ELSE 0 END) AS paid_3y,
               SUM(CASE WHEN year = {year + 1}
                        THEN COALESCE(paid, 0) ELSE 0 END) AS paid_next,
               SUM(CASE WHEN year BETWEEN {year + 1} AND {later}
                        THEN COALESCE(paid, 0) ELSE 0 END) AS paid_after
        FROM {relation}
        GROUP BY id_debito
        HAVING SUM(CASE WHEN {snap} THEN 1 ELSE 0 END) > 0
        """
    )


def _spill_year(con, year: int, parts: int) -> str:
    """Write one reference year to disk and return the path. The table can then be dropped."""
    import tempfile

    handle, name = tempfile.mkstemp(prefix="imp23-year-", suffix=".parquet")
    os.close(handle)
    quoted = name.replace("'", "''")
    con.execute(
        f"""
        COPY (
            SELECT s.id_debito, s.cnpj, s.cnpj_root, s.origin, s.enrol_date, s.situacao,
                   s.stock, s.enrolled_value, s.paid_5y, s.paid_3y,
                   s.paid_next, s.paid_after,
                   TRUE AS in_stock,
                   FALSE AS guaranteed,
                   COALESCE(f.match_nivel, 'nao_encontrado') AS match_nivel,
                   f.registry_code AS registry_code,
                   COALESCE(o.{_qident(f"open_{year}")}, FALSE) AS instalment_open,
                   (COALESCE(hash(s.cnpj_root), 0) & {parts - 1}) AS _part
            FROM year_snap s
            LEFT JOIN firms f ON s.cnpj = f.cnpj
            LEFT JOIN opens o ON s.id_debito = o.id_debito
        ) TO '{quoted}' (FORMAT PARQUET, COMPRESSION ZSTD)
        """
    )
    return name


def _backtest_sql(
    panel: pl.LazyFrame,
    firms: pl.DataFrame,
    instalments: pl.DataFrame | pl.LazyFrame,
    params: PgeParams,
    years: list[int],
):
    """One reference year at a time. The wide certificate table is not collected."""
    relation, temps = _acquire_relation(_lazy(panel).select(_FACT_COLUMNS))
    con = _duck()
    try:
        if _store_open_flags(con, instalments, years) is None:
            return None
        _BUILDS["ref_facts"] += 1
        _SQL_RUNS["backtest"] += 1
        con.register(
            "firms",
            firms.select(
                "cnpj",
                "match_nivel",
                pl.col("situacao_cadastral").alias("registry_code"),
            ),
        )
        # One root stays in one batch, so the unit's history is complete.
        # The stock is written to disk before it is classified, so DuckDB's
        # buffer is released first.
        n_parts = 8
        year_paths: list[tuple[int, str]] = []
        coverage: list[dict[str, Any]] = []
        for year in years:
            _store_year_snap(con, relation, year, params)
            counted = con.execute(
                "SELECT COUNT(*), COALESCE(SUM(stock), 0) FROM year_snap"
            ).fetchone()
            n_cda, stock = int(counted[0]), float(counted[1])
            coverage.append(_coverage_row(year, n_cda, stock))
            if n_cda == 0:
                con.execute("DROP TABLE IF EXISTS year_snap")
                continue
            year_paths.append((year, _spill_year(con, year, n_parts)))
            con.execute("DROP TABLE IF EXISTS year_snap")
    finally:
        con.close()
        _drop_files(temps)
    classes: list[pl.DataFrame] = []
    recovered: list[pl.DataFrame] = []
    n_sup = 0
    keep = [
        "id_debito",
        "cnpj",
        "origin",
        "specific",
        "binary_class",
        "stock",
        "paid_next",
        "paid_after",
        "recoverable",
    ]
    try:
        for year, year_path in year_paths:
            parts: list[pl.DataFrame] = []
            for part in range(n_parts):
                chunk = (
                    pl.scan_parquet(year_path)
                    .filter(pl.col("_part") == part)
                    .drop("_part")
                    .collect()
                )
                if chunk.height == 0:
                    continue
                classified = classify_pge(chunk, date(year, 12, 31), params)
                parts.append(classified.select(keep))
                del chunk, classified
            if not parts:
                continue
            classified = pl.concat(parts)
            summary, n1 = summarise_pge(classified, year)
            rec, n2 = _recovery_prepared(classified, year)
            classes.append(summary)
            recovered.append(rec)
            n_sup += n1 + n2
            del classified, parts
    finally:
        _drop_files([path for _, path in year_paths])
    empty_c = pl.DataFrame()
    empty_r = pl.DataFrame()
    return (
        pl.concat(classes, how="diagonal_relaxed") if classes else empty_c,
        pl.concat(recovered, how="diagonal_relaxed") if recovered else empty_r,
        {
            "n_cells_suppressed": n_sup,
            "coverage": pl.DataFrame(coverage),
        },
    )


def _coverage_row(year: int, n_cda: int, stock: float) -> dict[str, Any]:
    """One requested reference year. An empty December snapshot is excluded, not hidden."""
    included = n_cda > 0
    if not included:
        reason = (
            "Excluded. No December snapshot: 0 certificates with "
            "present_in_december and positive stock."
        )
    elif year == 2024:
        reason = (
            "Included. December 2024 is partial: a known gap in the DEBITO "
            "extract (EQ-05). Its level is not a complete December."
        )
    else:
        reason = "Included. December snapshot with positive stock."
    return {
        "ref_year": year,
        "included": included,
        "n_cda": int(n_cda),
        "stock": float(stock),
        "reason": reason,
    }


def backtest_rule(
    panel: pl.DataFrame | pl.LazyFrame,
    firms: pl.DataFrame,
    instalments: pl.DataFrame | pl.LazyFrame,
    params: PgeParams,
) -> tuple[pl.DataFrame, pl.DataFrame, dict[str, int]]:
    years = [year for year in params.ref_years if year <= LAST_YEAR]
    if _is_lazy(panel):
        reduced = _backtest_sql(panel, firms, instalments, params, years)
        if reduced is not None:
            return reduced
    facts = ref_facts(panel, params)
    opens = _open_flags(instalments, years)
    firm = firms.select(
        "cnpj",
        "match_nivel",
        pl.col("situacao_cadastral").alias("registry_code"),
    )
    classes: list[pl.DataFrame] = []
    recovered: list[pl.DataFrame] = []
    coverage: list[dict[str, Any]] = []
    n_sup = 0
    for year in years:
        snap = facts.filter(pl.col(f"n_snap_{year}") > 0).select(
            "id_debito",
            pl.col(f"cnpj_{year}").alias("cnpj"),
            pl.col(f"root_{year}").alias("cnpj_root"),
            pl.col(f"origin_{year}").alias("origin"),
            pl.col(f"enrol_{year}").alias("enrol_date"),
            pl.col(f"situacao_{year}").alias("situacao"),
            pl.col(f"stock_{year}").fill_null(0).alias("stock"),
            pl.col(f"enrolled_{year}").alias("enrolled_value"),
            pl.col(f"paid5_{year}").fill_null(0).alias("paid_5y"),
            pl.col(f"paid3_{year}").fill_null(0).alias("paid_3y"),
            pl.col(f"next_{year}").fill_null(0).alias("paid_next"),
            pl.col(f"after_{year}").fill_null(0).alias("paid_after"),
            pl.lit(True).alias("in_stock"),
            pl.lit(False).alias("guaranteed"),
        )
        n_cda = snap.height
        stock = float(snap["stock"].sum()) if n_cda else 0.0
        coverage.append(_coverage_row(year, n_cda, stock))
        if n_cda == 0:
            continue
        snap = (
            snap.join(firm, on="cnpj", how="left")
            .join(opens.select("id_debito", f"open_{year}"), on="id_debito", how="left")
            .with_columns(
                pl.col("match_nivel").fill_null("nao_encontrado"),
                pl.col(f"open_{year}").fill_null(False).alias("instalment_open"),
            )
        )
        classified = classify_pge(snap, date(year, 12, 31), params)
        summary, n1 = summarise_pge(classified, year)
        rec, n2 = _recovery_prepared(classified, year)
        classes.append(summary)
        recovered.append(rec)
        n_sup += n1 + n2
    empty_c = pl.DataFrame()
    empty_r = pl.DataFrame()
    return (
        pl.concat(classes, how="diagonal_relaxed") if classes else empty_c,
        pl.concat(recovered, how="diagonal_relaxed") if recovered else empty_r,
        {
            "n_cells_suppressed": n_sup,
            "coverage": pl.DataFrame(coverage),
        },
    )


def cda_index(panel: pl.DataFrame | pl.LazyFrame) -> pl.DataFrame:
    """One row per CDA in the analysis window. One streaming pass."""
    _BUILDS["cda_index"] += 1
    return _collect(
        _lazy(panel)
        .filter((pl.col("year") >= FIRST_YEAR) & (pl.col("year") <= LAST_YEAR))
        .select(
            "id_debito",
            "cnpj",
            "cnpj_root",
            "origin",
            "enrol_date",
            "enrol_year",
            "enrolled_value",
            "paid",
        )
        .group_by("id_debito")
        .agg(
            pl.col("cnpj").drop_nulls().first(),
            pl.col("cnpj_root").drop_nulls().first(),
            pl.col("origin").drop_nulls().first(),
            pl.col("enrol_date").min(),
            pl.col("enrol_year").min(),
            pl.col("enrolled_value").max(),
            pl.col("paid").sum().alias("paid"),
        )
    )


def _cohort_cdas(
    panel: pl.DataFrame | pl.LazyFrame, cdas: pl.DataFrame | None = None
) -> pl.DataFrame:
    """Certificate index for enrolments inside the window. Reuse ``cdas`` when given."""
    if cdas is None:
        cdas = cda_index(panel)
    return cdas.filter(
        pl.col("enrol_year").is_between(FIRST_YEAR, LAST_YEAR)
        & pl.col("enrol_date").is_not_null()
    )


def _semi_flag(
    events: pl.DataFrame | pl.LazyFrame, mask: pl.Expr, name: str
) -> pl.LazyFrame:
    """Distinct debt ids matching ``mask``, as a one-column flag. Not a Python list."""
    return (
        _lazy(events)
        .filter(mask)
        .select(pl.col("id_debito").drop_nulls())
        .unique()
        .with_columns(pl.lit(True).alias(name))
    )


def _with_semi_flag(
    cdas: pl.DataFrame,
    events: pl.DataFrame | pl.LazyFrame,
    mask: pl.Expr,
    name: str,
) -> pl.DataFrame:
    return _collect(
        cdas.lazy()
        .join(_semi_flag(events, mask, name), on="id_debito", how="left")
        .with_columns(pl.col(name).fill_null(False))
    )


def request_share(
    panel: pl.DataFrame | pl.LazyFrame,
    instalments: pl.DataFrame | pl.LazyFrame,
    *,
    cdas: pl.DataFrame | None = None,
) -> tuple[pl.DataFrame, int]:
    cdas = _cohort_cdas(panel, cdas)
    if _is_lazy(instalments):
        reduced = _share_sql(
            cdas,
            instalments,
            where=_window_sql("data_solicitacao"),
            flag="has_request",
            group_cols=["enrol_year"],
            metrics={
                "n_cda": "COUNT(*)",
                "n_with_request": "CAST(SUM(has_request) AS BIGINT)",
                "enrolled": "CAST(SUM(enrolled_value) AS DOUBLE)",
            },
            run_key="share",
        )
        if reduced is not None:
            out, n_sup = reduced
            out = out.with_columns(
                (pl.col("n_with_request") / pl.col("n_cda")).alias("share_with_request")
            )
            return assert_public(out), n_sup
    frame = _with_semi_flag(
        cdas, instalments, _plausible("data_solicitacao"), "has_request"
    )
    out, n_sup = suppress_aggregate(
        frame,
        ["enrol_year"],
        {
            "n_cda": pl.len(),
            "n_with_request": pl.col("has_request").sum(),
            "enrolled": pl.col("enrolled_value").sum(),
        },
        pool="origin",
    )
    out = out.with_columns(
        (pl.col("n_with_request") / pl.col("n_cda")).alias("share_with_request")
    )
    return assert_public(out), n_sup


def status_distribution(
    panel: pl.DataFrame | pl.LazyFrame,
    instalments: pl.DataFrame | pl.LazyFrame,
    *,
    cdas: pl.DataFrame | None = None,
) -> tuple[pl.DataFrame, int]:
    cdas = _cohort_cdas(panel, cdas).select("id_debito", "cnpj", "origin")
    if _is_lazy(instalments):
        reduced = _status_count_sql(
            cdas, instalments, "status_parcelamento", "n_requests"
        )
        if reduced is not None:
            return reduced
    # Collapse to debt × status before the firm join, so the raw request table is not held.
    compact = (
        _lazy(instalments)
        .group_by(
            "id_debito",
            pl.col("status_parcelamento").fill_null("missing status").alias("status"),
        )
        .agg(pl.len().alias("n_rows"))
    )
    req = compact.join(cdas.lazy(), on="id_debito", how="inner")
    out, n_sup = suppress_aggregate(
        req,
        ["origin"],
        {"n_requests": pl.col("n_rows").sum()},
        pool="status",
    )
    return assert_public(out), n_sup


def _month_diff(start: str, end: str) -> pl.Expr:
    return (pl.col(end).dt.year() - pl.col(start).dt.year()) * 12 + (
        pl.col(end).dt.month() - pl.col(start).dt.month()
    )


def kaplan_meier(counts: pl.DataFrame, group_cols: list[str]) -> pl.DataFrame:
    """1 − S(t) from aggregated (t, n, d). ``n`` is the count with observed time t."""
    frames = []
    if counts.height == 0:
        return counts
    groups = (
        counts.partition_by(group_cols, as_dict=True) if group_cols else {(): counts}
    )
    # polars partition_by as_dict keys are tuples
    if not group_cols:
        groups = {(): counts}
    for key, g in groups.items():
        g = g.sort("t")
        n = g["n"].to_numpy().astype(float)
        d = g["d"].to_numpy().astype(float)
        risk = np.cumsum(n[::-1])[::-1]
        with np.errstate(divide="ignore", invalid="ignore"):
            step = np.where(risk > 0, 1 - d / risk, np.nan)
        survival = np.cumprod(step)
        share = 1 - survival
        share = np.where(risk >= MIN_CNPJ, share, np.nan)
        row = {
            "t": g["t"].to_list(),
            "at_risk": risk.tolist(),
            "share": share.tolist(),
            "n_cnpj": g["n_cnpj"].to_list()
            if "n_cnpj" in g.columns
            else [None] * g.height,
        }
        if group_cols:
            values = key if isinstance(key, tuple) else (key,)
            for col, val in zip(group_cols, values, strict=True):
                row[col] = [val] * g.height
        frames.append(pl.DataFrame(row))
    return pl.concat(frames, how="diagonal_relaxed") if frames else counts.clear()


def _duration_km(
    events: pl.DataFrame, group_cols: list[str]
) -> tuple[pl.DataFrame, int]:
    """``events`` has one row per CDA: t, d, cnpj, and the group columns."""
    if events.height == 0:
        return events, 0
    raw = events.group_by(["t", *group_cols]).agg(
        pl.len().alias("n"),
        pl.col("d").sum().alias("d"),
        pl.col("cnpj").n_unique().alias("n_cnpj"),
    )
    sizes = events.group_by(group_cols).agg(
        pl.col("cnpj").n_unique().alias("n_cnpj_group")
    )
    small_groups = sizes.filter(pl.col("n_cnpj_group") < MIN_CNPJ)
    n_sup = small_groups.height
    kept = raw.join(
        sizes.filter(pl.col("n_cnpj_group") >= MIN_CNPJ), on=group_cols, how="inner"
    )
    curve = kaplan_meier(kept, group_cols)
    return assert_public(curve), n_sup


def km_enrol_to_request(
    panel: pl.DataFrame | pl.LazyFrame,
    instalments: pl.DataFrame | pl.LazyFrame,
    *,
    cdas: pl.DataFrame | None = None,
) -> tuple[pl.DataFrame, int, dict[str, int]]:
    cdas = _cohort_cdas(panel, cdas)
    if _is_lazy(instalments):
        reduced = _enrol_km_sql(
            cdas, instalments, "data_solicitacao", count_negative=True
        )
        if reduced is not None:
            return reduced
    first = _collect(
        _lazy(instalments)
        .filter(_plausible("data_solicitacao"))
        .group_by("id_debito")
        .agg(pl.col("data_solicitacao").min().alias("event"))
    )
    ev = (
        cdas.join(first, on="id_debito", how="left")
        .with_columns(
            pl.when(pl.col("event").is_not_null())
            .then(pl.col("event"))
            .otherwise(pl.lit(CENSOR))
            .alias("end"),
            pl.col("event").is_not_null().cast(pl.Int8).alias("d"),
        )
        .with_columns(_month_diff("enrol_date", "end").alias("t"))
    )
    n_neg = int((ev["t"] < 0).sum())
    ev = ev.filter(pl.col("t") >= 0)
    curve, n_sup = _duration_km(ev.select("t", "d", "cnpj", "origin"), ["origin"])
    return curve, n_sup, {"n_negative_duration": n_neg}


def km_request_to_break(
    instalments: pl.DataFrame | pl.LazyFrame,
    panel: pl.DataFrame | pl.LazyFrame,
    *,
    cdas: pl.DataFrame | None = None,
) -> tuple[pl.DataFrame, int]:
    cdas = _cohort_cdas(panel, cdas).select("id_debito", "cnpj", "origin")
    if _is_lazy(instalments):
        reduced = _break_km_sql(cdas, instalments)
        if reduced is not None:
            return reduced
    req = _collect(
        _lazy(instalments)
        .filter(_plausible("data_solicitacao"))
        .join(cdas.lazy(), on="id_debito", how="inner")
    )
    if req.height == 0:
        return req.clear(), 0
    req = req.with_columns(
        pl.when(_plausible("data_rompimento"))
        .then(pl.col("data_rompimento"))
        .otherwise(pl.lit(CENSOR))
        .alias("end"),
        _plausible("data_rompimento").cast(pl.Int8).alias("d"),
    ).with_columns(_month_diff("data_solicitacao", "end").alias("t"))
    req = req.filter(pl.col("t") >= 0)
    # One duration per request would publish request-level rows after aggregation.
    curve, n_sup = _duration_km(
        req.select(
            pl.col("t"),
            pl.col("d"),
            pl.col("cnpj"),
            pl.col("origin"),
        ),
        ["origin"],
    )
    return curve, n_sup


def break_rates(
    panel: pl.DataFrame | pl.LazyFrame,
    instalments: pl.DataFrame | pl.LazyFrame,
    *,
    cdas: pl.DataFrame | None = None,
) -> tuple[pl.DataFrame, pl.DataFrame, int]:
    cdas = _cohort_cdas(panel, cdas).select("id_debito", "cnpj", "origin")
    if _is_lazy(instalments):
        reduced = _break_rates_sql(cdas, instalments)
        if reduced is not None:
            return reduced
    req = _collect(
        _lazy(instalments)
        .filter(_plausible("data_solicitacao"))
        .join(cdas.lazy(), on="id_debito", how="inner")
    )
    req = req.with_columns(
        _plausible("data_rompimento").alias("broken"),
        parcel_band_expr(),
    )
    by_origin, n1 = suppress_aggregate(
        req,
        [],
        {"n_requests": pl.len(), "n_broken": pl.col("broken").sum()},
        pool="origin",
    )
    by_band, n2 = suppress_aggregate(
        req.filter(pl.col("parcel_band").is_not_null()),
        ["origin"],
        {"n_requests": pl.len(), "n_broken": pl.col("broken").sum()},
        pool="parcel_band",
    )
    by_origin = by_origin.with_columns(
        (pl.col("n_broken") / pl.col("n_requests")).alias("break_rate")
    )
    by_band = by_band.with_columns(
        (pl.col("n_broken") / pl.col("n_requests")).alias("break_rate")
    )
    return assert_public(by_origin), assert_public(by_band), n1 + n2


def recovery_by_flag(
    panel: pl.DataFrame | pl.LazyFrame,
    instalments: pl.DataFrame | pl.LazyFrame,
    *,
    flag: str,
    cdas: pl.DataFrame | None = None,
) -> tuple[pl.DataFrame, int]:
    """Recovery (paid / enrolled value) by origin and by a yes/no flag."""
    cdas = _cohort_cdas(panel, cdas)
    if flag == "request":
        mask = _plausible("data_solicitacao")
        label_yes, label_no = "with a request", "without a request"
    elif flag == "break":
        mask = _plausible("data_rompimento")
        label_yes, label_no = "with a break", "without a break"
    else:
        raise ValueError(flag)
    if _is_lazy(instalments):
        date_col = "data_solicitacao" if flag == "request" else "data_rompimento"
        reduced = _recovery_flag_sql(
            cdas,
            instalments,
            where=_window_sql(date_col),
            label_yes=label_yes,
            label_no=label_no,
        )
        if reduced is not None:
            return reduced
    flagged = _with_semi_flag(cdas, instalments, mask, "_hit")
    frame = flagged.with_columns(
        pl.when(pl.col("_hit"))
        .then(pl.lit(label_yes))
        .otherwise(pl.lit(label_no))
        .alias("group")
    )
    out, n_sup = suppress_aggregate(
        frame,
        ["origin"],
        {
            "n_cda": pl.len(),
            "paid": pl.col("paid").sum(),
            "enrolled": pl.col("enrolled_value").sum(),
        },
        pool="group",
    )
    out = out.with_columns((pl.col("paid") / pl.col("enrolled")).alias("recovery"))
    return assert_public(out), n_sup


def protest_share(
    panel: pl.DataFrame | pl.LazyFrame,
    protests: pl.DataFrame | pl.LazyFrame,
    *,
    cdas: pl.DataFrame | None = None,
) -> tuple[pl.DataFrame, int]:
    cdas = _cohort_cdas(panel, cdas)
    if _is_lazy(protests):
        reduced = _share_sql(
            cdas,
            protests,
            where="id_debito IS NOT NULL",
            flag="protested",
            group_cols=["enrol_year"],
            metrics={
                "n_cda": "COUNT(*)",
                "n_protested": "CAST(SUM(protested) AS BIGINT)",
                "enrolled": "CAST(SUM(enrolled_value) AS DOUBLE)",
                "enrolled_protested": (
                    "CAST(SUM(CASE WHEN protested = 1 THEN enrolled_value END) AS DOUBLE)"
                ),
            },
            run_key="share",
        )
        if reduced is not None:
            out, n_sup = reduced
            out = out.with_columns(
                (pl.col("n_protested") / pl.col("n_cda")).alias("share_protested")
            )
            return assert_public(out), n_sup
    frame = _with_semi_flag(
        cdas, protests, pl.col("id_debito").is_not_null(), "protested"
    )
    out, n_sup = suppress_aggregate(
        frame,
        ["enrol_year"],
        {
            "n_cda": pl.len(),
            "n_protested": pl.col("protested").sum(),
            "enrolled": pl.col("enrolled_value").sum(),
            "enrolled_protested": pl.col("enrolled_value")
            .filter(pl.col("protested"))
            .sum(),
        },
        pool="origin",
    )
    out = out.with_columns(
        (pl.col("n_protested") / pl.col("n_cda")).alias("share_protested")
    )
    return assert_public(out), n_sup


def km_enrol_to_protest(
    panel: pl.DataFrame | pl.LazyFrame,
    protests: pl.DataFrame | pl.LazyFrame,
    *,
    cdas: pl.DataFrame | None = None,
) -> tuple[pl.DataFrame, int]:
    cdas = _cohort_cdas(panel, cdas)
    if _is_lazy(protests):
        reduced = _enrol_km_sql(cdas, protests, "data_protesto", count_negative=False)
        if reduced is not None:
            return reduced
    first = _collect(
        _lazy(protests)
        .filter(_plausible("data_protesto"))
        .group_by("id_debito")
        .agg(pl.col("data_protesto").min().alias("event"))
    )
    ev = (
        cdas.join(first, on="id_debito", how="left")
        .with_columns(
            pl.when(pl.col("event").is_not_null())
            .then(pl.col("event"))
            .otherwise(pl.lit(CENSOR))
            .alias("end"),
            pl.col("event").is_not_null().cast(pl.Int8).alias("d"),
        )
        .with_columns(_month_diff("enrol_date", "end").alias("t"))
    )
    ev = ev.filter(pl.col("t") >= 0)
    curve, n_sup = _duration_km(ev.select("t", "d", "cnpj", "origin"), ["origin"])
    return curve, n_sup


def protest_status(
    panel: pl.DataFrame | pl.LazyFrame,
    protests: pl.DataFrame | pl.LazyFrame,
    *,
    cdas: pl.DataFrame | None = None,
) -> tuple[pl.DataFrame, int]:
    cdas = _cohort_cdas(panel, cdas).select("id_debito", "cnpj", "origin")
    if _is_lazy(protests):
        reduced = _status_count_sql(cdas, protests, "status_protesto", "n_rows")
        if reduced is not None:
            return reduced
    compact = (
        _lazy(protests)
        .group_by(
            "id_debito",
            pl.col("status_protesto").fill_null("missing status").alias("status"),
        )
        .agg(pl.len().alias("n_rows"))
    )
    rows = compact.join(cdas.lazy(), on="id_debito", how="inner")
    out, n_sup = suppress_aggregate(
        rows,
        ["origin"],
        {"n_rows": pl.col("n_rows").sum()},
        pool="status",
    )
    return assert_public(out), n_sup


def _id_eq(left: str, right: str) -> str:
    return f"CAST({left}.id_debito AS BIGINT) = CAST({right}.id_debito AS BIGINT)"


def _recovery_around_sql(
    cdas: pl.DataFrame,
    protests: pl.DataFrame | pl.LazyFrame,
    payments: pl.DataFrame | pl.LazyFrame,
) -> tuple[pl.DataFrame, int] | None:
    """Keep GARE on disk and aggregate it only for ids that have a protest date.

    The protest table is reduced to one date per debt before any payment row is
    read. Payment rows are not collected into Polars.
    """
    con = _duck()
    try:
        protest_sql = _sql_relation(con, protests, "protests")
        pay_sql = _sql_relation(con, payments, "payments")
        if protest_sql is None or pay_sql is None:
            return None
        con.register(
            "cohort",
            cdas.select("id_debito", "cnpj", "origin", "enrolled_value"),
        )
        con.execute(
            f"""
            CREATE TEMP TABLE dated AS
            SELECT CAST(p.id_debito AS BIGINT) AS id_debito,
                   MIN(p.data_protesto) AS protest_date
            FROM {protest_sql} p
            INNER JOIN cohort c ON {_id_eq("p", "c")}
            WHERE {_window_sql("p.data_protesto")}
            GROUP BY 1
            """
        )
        con.execute(
            f"""
            CREATE TEMP TABLE paid AS
            SELECT d.id_debito,
                   SUM(CASE WHEN g.pay_date < d.protest_date THEN g.amount ELSE 0 END)
                       AS paid_before,
                   SUM(CASE WHEN g.pay_date >= d.protest_date THEN g.amount ELSE 0 END)
                       AS paid_after
            FROM {pay_sql} g
            INNER JOIN dated d ON CAST(g.id_debito AS BIGINT) = d.id_debito
            WHERE {_window_sql("g.pay_date")}
            GROUP BY d.id_debito
            """
        )
        con.execute(
            """
            CREATE TEMP TABLE base_paid AS
            SELECT c.origin, c.cnpj, c.enrolled_value,
                   COALESCE(p.paid_before, 0) AS paid_before,
                   COALESCE(p.paid_after, 0) AS paid_after
            FROM cohort c
            INNER JOIN dated d ON CAST(c.id_debito AS BIGINT) = d.id_debito
            LEFT JOIN paid p ON d.id_debito = p.id_debito
            """
        )
        out, n_sup = _suppress_sql(
            con,
            "base_paid",
            [],
            {
                "n_cda": "COUNT(*)",
                "paid_before": "CAST(SUM(paid_before) AS DOUBLE)",
                "paid_after": "CAST(SUM(paid_after) AS DOUBLE)",
                "enrolled": "CAST(SUM(enrolled_value) AS DOUBLE)",
            },
            pool="origin",
        )
    finally:
        con.close()
    _SQL_RUNS["recovery"] += 1
    out = out.with_columns(
        (pl.col("paid_before") / pl.col("enrolled")).alias("recovery_before"),
        (pl.col("paid_after") / pl.col("enrolled")).alias("recovery_after"),
    )
    return assert_public(out), n_sup


def _date_quality_sql(df: pl.LazyFrame, col: str) -> dict[str, Any] | None:
    relation = _compile_relation(df)
    if relation is None:
        return None
    quoted = _qident(col)
    start = WINDOW_START.isoformat()
    end = WINDOW_END.isoformat()
    con = _duck()
    try:
        row = con.execute(
            f"""
            SELECT COUNT(*) AS n,
                   COUNT(*) FILTER (WHERE {quoted} IS NULL) AS n_null,
                   COUNT(*) FILTER (
                       WHERE {quoted} IS NOT NULL AND {quoted} < DATE '{start}'
                   ) AS n_before_window,
                   COUNT(*) FILTER (
                       WHERE {quoted} IS NOT NULL AND {quoted} > DATE '{end}'
                   ) AS n_after_window,
                   COUNT(*) FILTER (
                       WHERE {quoted} IS NOT NULL AND year({quoted}) < 1000
                   ) AS n_year_before_1000
            FROM {relation}
            """
        ).fetchone()
    finally:
        con.close()
    _SQL_RUNS["dates"] += 1
    keys = (
        "n",
        "n_null",
        "n_before_window",
        "n_after_window",
        "n_year_before_1000",
    )
    return {
        "column": col,
        **{key: int(value or 0) for key, value in zip(keys, row, strict=True)},
    }


def _distinct_ids(relation: str, where: str) -> str:
    return (
        f"(SELECT DISTINCT CAST(id_debito AS BIGINT) AS id_debito "
        f"FROM {relation} WHERE {where})"
    )


def _share_sql(
    cdas: pl.DataFrame,
    events: pl.DataFrame | pl.LazyFrame,
    *,
    where: str,
    flag: str,
    group_cols: list[str],
    metrics: dict[str, str],
    run_key: str,
) -> tuple[pl.DataFrame, int] | None:
    con = _duck()
    try:
        relation = _sql_relation(con, events, "events")
        if relation is None:
            return None
        con.register("cohort", cdas)
        con.execute(
            f"""
            CREATE TEMP VIEW detail AS
            SELECT c.*, CASE WHEN h.id_debito IS NULL THEN 0 ELSE 1 END AS {flag}
            FROM cohort c
            LEFT JOIN {_distinct_ids(relation, where)} h
              ON CAST(c.id_debito AS BIGINT) = h.id_debito
            """
        )
        out, n_sup = _suppress_sql(con, "detail", group_cols, metrics, pool="origin")
    finally:
        con.close()
    _SQL_RUNS[run_key] += 1
    return out, n_sup


def _status_count_sql(
    cdas: pl.DataFrame,
    events: pl.DataFrame | pl.LazyFrame,
    status_col: str,
    metric: str,
) -> tuple[pl.DataFrame, int] | None:
    con = _duck()
    try:
        relation = _sql_relation(con, events, "events")
        if relation is None:
            return None
        con.register("cohort", cdas.select("id_debito", "cnpj", "origin"))
        quoted = _qident(status_col)
        con.execute(
            f"""
            CREATE TEMP VIEW detail AS
            SELECT c.origin, c.cnpj,
                   COALESCE(CAST(e.{quoted} AS VARCHAR), 'missing status') AS status
            FROM {relation} e
            INNER JOIN cohort c ON {_id_eq("e", "c")}
            """
        )
        out, n_sup = _suppress_sql(
            con,
            "detail",
            ["origin"],
            {metric: "COUNT(*)"},
            pool="status",
        )
    finally:
        con.close()
    _SQL_RUNS["status"] += 1
    return assert_public(out), n_sup


def _km_from_counts(
    raw: pl.DataFrame, sizes: pl.DataFrame, group_cols: list[str]
) -> tuple[pl.DataFrame, int]:
    if raw.height == 0:
        return assert_public(raw), 0
    small = sizes.filter(pl.col("n_cnpj_group") < MIN_CNPJ)
    kept = raw.join(
        sizes.filter(pl.col("n_cnpj_group") >= MIN_CNPJ),
        on=group_cols,
        how="inner",
    )
    return assert_public(kaplan_meier(kept, group_cols)), small.height


def _km_aggregate(con, group_cols: list[str]) -> tuple[pl.DataFrame, pl.DataFrame]:
    keys = ", ".join(_qident(col) for col in group_cols)
    raw = con.execute(
        f"""
        SELECT {keys}, t, COUNT(*) AS n, SUM(d) AS d, {_n_distinct_sql()} AS n_cnpj
        FROM events
        WHERE t >= 0
        GROUP BY {keys}, t
        """
    ).pl()
    sizes = con.execute(
        f"""
        SELECT {keys}, {_n_distinct_sql()} AS n_cnpj_group
        FROM events
        WHERE t >= 0
        GROUP BY {keys}
        """
    ).pl()
    return raw, sizes


def _enrol_km_sql(
    cdas: pl.DataFrame,
    events: pl.DataFrame | pl.LazyFrame,
    date_col: str,
    *,
    count_negative: bool,
) -> tuple | None:
    con = _duck()
    try:
        relation = _sql_relation(con, events, "events_raw")
        if relation is None:
            return None
        con.register(
            "cohort",
            cdas.select("id_debito", "cnpj", "origin", "enrol_date"),
        )
        quoted = _qident(date_col)
        censor = CENSOR.isoformat()
        con.execute(
            f"""
            CREATE TEMP VIEW events AS
            SELECT origin, cnpj, d,
                   {_month_sql("enrol_date", "end_date")} AS t
            FROM (
                SELECT c.origin, c.cnpj, c.enrol_date,
                       CASE WHEN h.event IS NULL THEN DATE '{censor}'
                            ELSE h.event END AS end_date,
                       CASE WHEN h.event IS NULL THEN 0 ELSE 1 END AS d
                FROM cohort c
                LEFT JOIN (
                    SELECT CAST(id_debito AS BIGINT) AS id_debito,
                           MIN({quoted}) AS event
                    FROM {relation}
                    WHERE {_window_sql(quoted)}
                    GROUP BY 1
                ) h ON CAST(c.id_debito AS BIGINT) = h.id_debito
            )
            """
        )
        n_neg = 0
        if count_negative:
            n_neg = int(
                con.execute(
                    "SELECT COUNT(*) FILTER (WHERE t < 0) FROM events"
                ).fetchone()[0]
                or 0
            )
        raw, sizes = _km_aggregate(con, ["origin"])
    finally:
        con.close()
    _SQL_RUNS["km"] += 1
    curve, n_sup = _km_from_counts(raw, sizes, ["origin"])
    if count_negative:
        return curve, n_sup, {"n_negative_duration": n_neg}
    return curve, n_sup


def _break_km_sql(
    cdas: pl.DataFrame, instalments: pl.DataFrame | pl.LazyFrame
) -> tuple[pl.DataFrame, int] | None:
    con = _duck()
    try:
        relation = _sql_relation(con, instalments, "instalments")
        if relation is None:
            return None
        con.register("cohort", cdas.select("id_debito", "cnpj", "origin"))
        censor = CENSOR.isoformat()
        plausible_break = _window_sql("data_rompimento")
        con.execute(
            f"""
            CREATE TEMP VIEW events AS
            SELECT origin, cnpj, d,
                   {_month_sql("data_solicitacao", "end_date")} AS t
            FROM (
                SELECT c.origin, c.cnpj, e.data_solicitacao,
                       CASE WHEN {plausible_break} THEN e.data_rompimento
                            ELSE DATE '{censor}' END AS end_date,
                       CASE WHEN {plausible_break} THEN 1 ELSE 0 END AS d
                FROM {relation} e
                INNER JOIN cohort c ON {_id_eq("e", "c")}
                WHERE {_window_sql("e.data_solicitacao")}
            )
            """
        )
        raw, sizes = _km_aggregate(con, ["origin"])
    finally:
        con.close()
    _SQL_RUNS["km"] += 1
    return _km_from_counts(raw, sizes, ["origin"])


def _break_rates_sql(
    cdas: pl.DataFrame, instalments: pl.DataFrame | pl.LazyFrame
) -> tuple[pl.DataFrame, pl.DataFrame, int] | None:
    con = _duck()
    try:
        relation = _sql_relation(con, instalments, "instalments")
        if relation is None:
            return None
        con.register("cohort", cdas.select("id_debito", "cnpj", "origin"))
        plausible_break = _window_sql("e.data_rompimento")
        con.execute(
            f"""
            CREATE TEMP VIEW req AS
            SELECT c.origin, c.cnpj,
                   CASE WHEN {plausible_break} THEN 1 ELSE 0 END AS broken,
                   {_parcel_case("e.qtd_parcelas")} AS parcel_band
            FROM {relation} e
            INNER JOIN cohort c ON {_id_eq("e", "c")}
            WHERE {_window_sql("e.data_solicitacao")}
            """
        )
        by_origin, n1 = _suppress_sql(
            con,
            "req",
            [],
            {
                "n_requests": "COUNT(*)",
                "n_broken": "CAST(SUM(broken) AS BIGINT)",
            },
            pool="origin",
        )
        con.execute(
            """
            CREATE TEMP VIEW req_band AS
            SELECT origin, cnpj, broken, parcel_band
            FROM req
            WHERE parcel_band IS NOT NULL
            """
        )
        by_band, n2 = _suppress_sql(
            con,
            "req_band",
            ["origin"],
            {
                "n_requests": "COUNT(*)",
                "n_broken": "CAST(SUM(broken) AS BIGINT)",
            },
            pool="parcel_band",
        )
    finally:
        con.close()
    _SQL_RUNS["break"] += 1
    by_origin = by_origin.with_columns(
        (pl.col("n_broken") / pl.col("n_requests")).alias("break_rate")
    )
    by_band = by_band.with_columns(
        (pl.col("n_broken") / pl.col("n_requests")).alias("break_rate")
    )
    return assert_public(by_origin), assert_public(by_band), n1 + n2


def _recovery_flag_sql(
    cdas: pl.DataFrame,
    instalments: pl.DataFrame | pl.LazyFrame,
    *,
    where: str,
    label_yes: str,
    label_no: str,
) -> tuple[pl.DataFrame, int] | None:
    con = _duck()
    try:
        relation = _sql_relation(con, instalments, "instalments")
        if relation is None:
            return None
        con.register(
            "cohort",
            cdas.select("id_debito", "cnpj", "origin", "paid", "enrolled_value"),
        )
        yes = label_yes.replace("'", "''")
        no = label_no.replace("'", "''")
        con.execute(
            f"""
            CREATE TEMP VIEW detail AS
            SELECT c.origin, c.cnpj, c.paid, c.enrolled_value,
                   CASE WHEN h.id_debito IS NULL THEN '{no}' ELSE '{yes}' END
                       AS {_qident("group")}
            FROM cohort c
            LEFT JOIN {_distinct_ids(relation, where)} h
              ON CAST(c.id_debito AS BIGINT) = h.id_debito
            """
        )
        out, n_sup = _suppress_sql(
            con,
            "detail",
            ["origin"],
            {
                "n_cda": "COUNT(*)",
                "paid": "CAST(SUM(paid) AS DOUBLE)",
                "enrolled": "CAST(SUM(enrolled_value) AS DOUBLE)",
            },
            pool="group",
        )
    finally:
        con.close()
    _SQL_RUNS["share"] += 1
    out = out.with_columns((pl.col("paid") / pl.col("enrolled")).alias("recovery"))
    return assert_public(out), n_sup


def recovery_around_protest(
    panel: pl.DataFrame | pl.LazyFrame,
    protests: pl.DataFrame | pl.LazyFrame,
    payments: pl.DataFrame | pl.LazyFrame,
    *,
    cdas: pl.DataFrame | None = None,
) -> tuple[pl.DataFrame, int]:
    """Paid before the protest date and paid on or after it, over enrolled value."""
    cdas = _cohort_cdas(panel, cdas)
    if _is_lazy(protests) or _is_lazy(payments):
        reduced = _recovery_around_sql(cdas, protests, payments)
        if reduced is not None:
            return reduced
    dated = _collect(
        _lazy(protests)
        .filter(_plausible("data_protesto"))
        .group_by("id_debito")
        .agg(pl.col("data_protesto").min().alias("protest_date"))
    )
    base = cdas.join(dated, on="id_debito", how="inner")
    pay = (
        _lazy(payments)
        .filter(_plausible("pay_date"))
        .select("id_debito", "pay_date", "amount")
    )
    joined = pay.join(
        base.lazy().select("id_debito", "protest_date"), on="id_debito", how="inner"
    )
    sums = _collect(
        joined.group_by("id_debito").agg(
            pl.col("amount")
            .filter(pl.col("pay_date") < pl.col("protest_date"))
            .sum()
            .alias("paid_before"),
            pl.col("amount")
            .filter(pl.col("pay_date") >= pl.col("protest_date"))
            .sum()
            .alias("paid_after"),
        )
    )
    frame = base.join(sums, on="id_debito", how="left").with_columns(
        pl.col("paid_before").fill_null(0),
        pl.col("paid_after").fill_null(0),
        pl.lit("with a protest date").alias("group"),
    )
    out, n_sup = suppress_aggregate(
        frame,
        [],
        {
            "n_cda": pl.len(),
            "paid_before": pl.col("paid_before").sum(),
            "paid_after": pl.col("paid_after").sum(),
            "enrolled": pl.col("enrolled_value").sum(),
        },
        pool="origin",
    )
    out = out.with_columns(
        (pl.col("paid_before") / pl.col("enrolled")).alias("recovery_before"),
        (pl.col("paid_after") / pl.col("enrolled")).alias("recovery_after"),
    )
    return assert_public(out), n_sup


def instalment_page(
    panel: pl.DataFrame | pl.LazyFrame,
    instalments: pl.DataFrame | pl.LazyFrame,
    protests: pl.DataFrame | pl.LazyFrame,
    payments: pl.DataFrame | pl.LazyFrame,
    *,
    cdas: pl.DataFrame | None = None,
) -> dict[str, Any]:
    """Every instalment and protest table from one certificate index."""
    cohort = _cohort_cdas(panel, cdas)
    return {
        "cohort": cohort,
        "request_share": request_share(panel, instalments, cdas=cohort),
        "status": status_distribution(panel, instalments, cdas=cohort),
        "km_request": km_enrol_to_request(panel, instalments, cdas=cohort),
        "km_break": km_request_to_break(instalments, panel, cdas=cohort),
        "break_rates": break_rates(panel, instalments, cdas=cohort),
        "recovery_request": recovery_by_flag(
            panel, instalments, flag="request", cdas=cohort
        ),
        "recovery_break": recovery_by_flag(
            panel, instalments, flag="break", cdas=cohort
        ),
        "protest_share": protest_share(panel, protests, cdas=cohort),
        "km_protest": km_enrol_to_protest(panel, protests, cdas=cohort),
        "protest_status": protest_status(panel, protests, cdas=cohort),
        "protest_recovery": recovery_around_protest(
            panel, protests, payments, cdas=cohort
        ),
    }


def _with_firm(panel: pl.DataFrame | pl.LazyFrame, firms: pl.DataFrame) -> pl.LazyFrame:
    reject_person_columns(firms)
    firm = firms.select(
        "cnpj",
        "match_nivel",
        pl.col("situacao_cadastral").alias("registry_code"),
        "cnae_divisao",
        "porte",
    )
    return (
        _lazy(panel)
        .filter((pl.col("year") >= FIRST_YEAR) & (pl.col("year") <= LAST_YEAR))
        .join(firm.lazy(), on="cnpj", how="left")
        .with_columns(
            pl.col("match_nivel").fill_null("nao_encontrado"),
            _status_expr(),
            age_band_expr(),
        )
    )


def _age_frame(
    panel: pl.DataFrame | pl.LazyFrame,
    firms: pl.DataFrame,
    base: pl.DataFrame | None = None,
) -> pl.DataFrame | pl.LazyFrame:
    """Narrow certificate-year frame. ``base`` is reused so the panel is scanned once."""
    if base is not None:
        return base
    return _with_firm(panel, firms).select(
        "id_debito",
        "year",
        "cnpj",
        "origin",
        "stock",
        "paid",
        "age",
        "age_band",
        "enrol_year",
        "enrolled_value",
        "status",
    )


_AGE_COLUMNS = (
    "id_debito",
    "year",
    "cnpj",
    "origin",
    "stock",
    "paid",
    "age",
    "enrol_year",
    "enrolled_value",
)


def _bind_age(con, panel: pl.LazyFrame, firms: pl.DataFrame) -> list[str] | None:
    """Register firms and build ``age_rows``. Returns temp files, or None."""
    reject_person_columns(firms)
    relation, temps = _acquire_relation(_lazy(panel).select(_AGE_COLUMNS))
    con.register(
        "firms",
        firms.select(
            "cnpj",
            "match_nivel",
            pl.col("situacao_cadastral").alias("registry_code"),
        ),
    )
    listed = ", ".join(f"p.{_qident(col)}" for col in _AGE_COLUMNS)
    con.execute(
        f"""
        CREATE TEMP VIEW age_rows AS
        SELECT {listed},
               {_age_case("p.age")} AS age_band,
               {_status_case()} AS status
        FROM {relation} p
        LEFT JOIN firms f ON p.cnpj = f.cnpj
        WHERE p.year >= {FIRST_YEAR} AND p.year <= {LAST_YEAR}
        """
    )
    return temps


def _rate_frame(frame: pl.DataFrame) -> pl.DataFrame:
    return frame.with_columns((pl.col("paid") / pl.col("stock")).alias("rate"))


def _annual_sql(
    panel: pl.LazyFrame, firms: pl.DataFrame
) -> tuple[pl.DataFrame, int] | None:
    con = _duck()
    temps: list[str] = []
    try:
        bound = _bind_age(con, panel, firms)
        if bound is None:
            return None
        temps = bound
        con.execute(
            """
            CREATE TEMP VIEW annual_src AS
            SELECT id_debito, year, cnpj, origin, stock, paid, age_band
            FROM age_rows
            WHERE stock > 0 AND age_band IS NOT NULL
            """
        )
        out, n_sup = _suppress_sql(
            con,
            "annual_src",
            ["year", "age_band"],
            {
                "n_cda": _n_distinct_sql("id_debito"),
                "paid": "CAST(SUM(paid) AS DOUBLE)",
                "stock": "CAST(SUM(stock) AS DOUBLE)",
            },
            pool="origin",
        )
    finally:
        con.close()
        _drop_files(temps)
    _SQL_RUNS["age"] += 1
    out = _rate_frame(out)
    years = set(out["year"].drop_nulls().to_list())
    if any(year > LAST_YEAR for year in years):
        raise ValueError("annual recovery includes a year after the window")
    return assert_public(out), n_sup


def _mean_sql(
    panel: pl.LazyFrame, firms: pl.DataFrame
) -> tuple[pl.DataFrame, int] | None:
    con = _duck()
    temps: list[str] = []
    try:
        bound = _bind_age(con, panel, firms)
        if bound is None:
            return None
        temps = bound
        con.execute(
            """
            CREATE TEMP VIEW mean_src AS
            SELECT id_debito, cnpj, origin, stock, paid, age_band
            FROM age_rows
            WHERE stock > 0 AND age_band IS NOT NULL
            """
        )
        out, n_sup = _suppress_sql(
            con,
            "mean_src",
            ["age_band"],
            {
                "paid": "CAST(SUM(paid) AS DOUBLE)",
                "stock": "CAST(SUM(stock) AS DOUBLE)",
                "n_cda": _n_distinct_sql("id_debito"),
            },
            pool="origin",
        )
    finally:
        con.close()
        _drop_files(temps)
    _SQL_RUNS["age"] += 1
    return assert_public(_rate_frame(out)), n_sup


def _status_age_sql(
    panel: pl.LazyFrame, firms: pl.DataFrame
) -> tuple[pl.DataFrame, int] | None:
    con = _duck()
    temps: list[str] = []
    try:
        bound = _bind_age(con, panel, firms)
        if bound is None:
            return None
        temps = bound
        con.execute(
            """
            CREATE TEMP VIEW status_src AS
            SELECT id_debito, cnpj, origin, stock, paid, age_band, status
            FROM age_rows
            WHERE stock > 0 AND age_band IS NOT NULL
            """
        )
        out, n_sup = _suppress_sql(
            con,
            "status_src",
            ["age_band", "origin"],
            {
                "paid": "CAST(SUM(paid) AS DOUBLE)",
                "stock": "CAST(SUM(stock) AS DOUBLE)",
                "n_cda": _n_distinct_sql("id_debito"),
            },
            pool="status",
        )
    finally:
        con.close()
        _drop_files(temps)
    _SQL_RUNS["age"] += 1
    return assert_public(_rate_frame(out)), n_sup


def _unobserved_band(out: pl.DataFrame) -> pl.DataFrame:
    cohorts = out["enrol_year"].unique().drop_nulls().to_list()
    if not cohorts:
        return out
    gap = pl.DataFrame(
        {
            "enrol_year": cohorts,
            "age_band": [AGE_LABELS[-1]] * len(cohorts),
            "origin": ["not observable"] * len(cohorts),
            "n_cda": [None] * len(cohorts),
            "cum_paid": [None] * len(cohorts),
            "enrolled": [None] * len(cohorts),
            "n_cnpj": [None] * len(cohorts),
            "cumulative": [None] * len(cohorts),
        }
    )
    return pl.concat([out, gap], how="diagonal_relaxed")


def _cumulative_sql(panel: pl.LazyFrame) -> tuple[pl.DataFrame, int] | None:
    con = _duck()
    temps: list[str] = []
    try:
        relation, temps = _acquire_relation(
            _lazy(panel).select(
                "id_debito",
                "year",
                "cnpj",
                "origin",
                "paid",
                "age",
                "enrol_year",
                "enrolled_value",
            )
        )
        con.execute(
            f"""
            CREATE TEMP VIEW cum_last AS
            SELECT id_debito, age_band,
                   MAX(CASE WHEN rn = n_in THEN cum_paid END) AS cum_paid,
                   MAX(enrolled_value) AS enrolled_value,
                   MAX(CASE WHEN rn = n_in THEN origin END) AS origin,
                   MAX(CASE WHEN rn = n_in THEN cnpj END) AS cnpj,
                   MIN(enrol_year) AS enrol_year
            FROM (
                SELECT id_debito, age_band, enrolled_value, origin, cnpj, enrol_year,
                       SUM(COALESCE(paid, 0)) OVER (
                           PARTITION BY id_debito ORDER BY year
                       ) AS cum_paid,
                       ROW_NUMBER() OVER (
                           PARTITION BY id_debito, age_band ORDER BY age, year
                       ) AS rn,
                       COUNT(*) OVER (PARTITION BY id_debito, age_band) AS n_in
                FROM (
                    SELECT id_debito, year, cnpj, origin, paid, age, enrol_year,
                           enrolled_value, {_age_case("age")} AS age_band
                    FROM {relation}
                    WHERE enrol_year BETWEEN {FIRST_YEAR} AND {LAST_YEAR}
                      AND year BETWEEN {FIRST_YEAR} AND {LAST_YEAR}
                      AND year >= enrol_year
                ) tagged
                WHERE age_band IS NOT NULL AND age_band <> '{AGE_LABELS[-1]}'
            ) ordered
            GROUP BY id_debito, age_band
            """
        )
        out, n_sup = _suppress_sql(
            con,
            "cum_last",
            ["enrol_year", "age_band"],
            {
                "n_cda": "COUNT(*)",
                "cum_paid": "CAST(SUM(cum_paid) AS DOUBLE)",
                "enrolled": "CAST(SUM(enrolled_value) AS DOUBLE)",
            },
            pool="origin",
        )
    finally:
        con.close()
        _drop_files(temps)
    _SQL_RUNS["age"] += 1
    out = out.with_columns(
        (pl.col("cum_paid") / pl.col("enrolled")).alias("cumulative")
    )
    return assert_public(_unobserved_band(out)), n_sup


def stock_bases(panel: pl.DataFrame | pl.LazyFrame) -> pl.DataFrame:
    """Positive-stock year versus the December snapshot, one row per year.

    The annual rate uses the first. The collectable rule uses the second.
    """
    positive = pl.col("stock") > 0
    december = positive & pl.col("present_in_december").fill_null(False)
    out = _collect(
        _lazy(panel)
        .filter((pl.col("year") >= FIRST_YEAR) & (pl.col("year") <= LAST_YEAR))
        .group_by("year")
        .agg(
            pl.col("id_debito").filter(positive).n_unique().alias("n_positive"),
            pl.col("stock").filter(positive).sum().alias("stock_positive"),
            pl.col("id_debito").filter(december).n_unique().alias("n_december"),
            pl.col("stock").filter(december).sum().alias("stock_december"),
        )
        .with_columns(
            (pl.col("n_positive") - pl.col("n_december")).alias("n_not_in_december"),
            (pl.col("stock_positive") - pl.col("stock_december")).alias(
                "stock_not_in_december"
            ),
        )
        .sort("year")
    )
    return assert_public(out)


def annual_recovery(
    panel: pl.DataFrame | pl.LazyFrame,
    firms: pl.DataFrame,
    *,
    base: pl.DataFrame | None = None,
) -> tuple[pl.DataFrame, int]:
    """Non-cumulative paid/stock by year, age band and origin."""
    if base is None and _is_lazy(panel):
        reduced = _annual_sql(panel, firms)
        if reduced is not None:
            return reduced
    lf = _lazy(_age_frame(panel, firms, base)).filter(
        (pl.col("stock") > 0) & pl.col("age_band").is_not_null()
    )
    out, n_sup = suppress_aggregate(
        lf,
        ["year", "age_band"],
        {
            "n_cda": pl.col("id_debito").n_unique(),
            "paid": pl.col("paid").sum(),
            "stock": pl.col("stock").sum(),
        },
        pool="origin",
    )
    out = out.with_columns((pl.col("paid") / pl.col("stock")).alias("rate"))
    years = set(out["year"].drop_nulls().to_list())
    if any(y > LAST_YEAR for y in years):
        raise ValueError("annual recovery includes a year after the window")
    return assert_public(out), n_sup


def mean_recovery(
    panel: pl.DataFrame | pl.LazyFrame,
    firms: pl.DataFrame,
    *,
    base: pl.DataFrame | None = None,
) -> tuple[pl.DataFrame, int]:
    """Stock-weighted mean of the annual rate, by age band and origin."""
    if base is None and _is_lazy(panel):
        reduced = _mean_sql(panel, firms)
        if reduced is not None:
            return reduced
    lf = _lazy(_age_frame(panel, firms, base)).filter(
        (pl.col("stock") > 0) & pl.col("age_band").is_not_null()
    )
    out, n_sup = suppress_aggregate(
        lf,
        ["age_band"],
        {
            "paid": pl.col("paid").sum(),
            "stock": pl.col("stock").sum(),
            "n_cda": pl.col("id_debito").n_unique(),
        },
        pool="origin",
    )
    out = out.with_columns((pl.col("paid") / pl.col("stock")).alias("rate"))
    return assert_public(out), n_sup


def recovery_by_status(
    panel: pl.DataFrame | pl.LazyFrame,
    firms: pl.DataFrame,
    *,
    base: pl.DataFrame | None = None,
) -> tuple[pl.DataFrame, int]:
    """Stock-weighted recovery by age band, origin and registration status."""
    if base is None and _is_lazy(panel):
        reduced = _status_age_sql(panel, firms)
        if reduced is not None:
            return reduced
    lf = _lazy(_age_frame(panel, firms, base)).filter(
        (pl.col("stock") > 0) & pl.col("age_band").is_not_null()
    )
    out, n_sup = suppress_aggregate(
        lf,
        ["age_band", "origin"],
        {
            "paid": pl.col("paid").sum(),
            "stock": pl.col("stock").sum(),
            "n_cda": pl.col("id_debito").n_unique(),
        },
        pool="status",
    )
    out = out.with_columns((pl.col("paid") / pl.col("stock")).alias("rate"))
    return assert_public(out), n_sup


def cumulative_by_band(
    panel: pl.DataFrame | pl.LazyFrame,
    *,
    base: pl.DataFrame | None = None,
) -> tuple[pl.DataFrame, int]:
    """Cumulative paid / enrolled value at the last observed age inside each band.

    Cohorts are enrolments inside the window. The band above ten years is emitted
    empty: those cohorts do not reach it.
    """
    if base is None and _is_lazy(panel):
        reduced = _cumulative_sql(panel)
        if reduced is not None:
            return reduced
    source = _lazy(panel) if base is None else _lazy(base)
    lf = source.filter(
        pl.col("enrol_year").is_between(FIRST_YEAR, LAST_YEAR)
        & (pl.col("year") >= FIRST_YEAR)
        & (pl.col("year") <= LAST_YEAR)
        & (pl.col("year") >= pl.col("enrol_year"))
    ).with_columns(age_band_expr())
    lf = lf.filter(
        pl.col("age_band").is_not_null() & (pl.col("age_band") != AGE_LABELS[-1])
    )
    ordered = lf.sort(["id_debito", "year"]).with_columns(
        pl.col("paid").fill_null(0).cum_sum().over("id_debito").alias("cum_paid")
    )
    # Last observation of each CDA inside each band.
    last = (
        ordered.sort(["id_debito", "age_band", "age"])
        .group_by(["id_debito", "age_band"])
        .agg(
            pl.col("cum_paid").last(),
            pl.col("enrolled_value").max(),
            pl.col("origin").last(),
            pl.col("cnpj").last(),
            pl.col("enrol_year").min(),
        )
    )
    out, n_sup = suppress_aggregate(
        last,
        ["enrol_year", "age_band"],
        {
            "n_cda": pl.len(),
            "cum_paid": pl.col("cum_paid").sum(),
            "enrolled": pl.col("enrolled_value").sum(),
        },
        pool="origin",
    )
    out = out.with_columns(
        (pl.col("cum_paid") / pl.col("enrolled")).alias("cumulative")
    )
    # Empty >10 rows, one per cohort that exists, so the gap is visible.
    cohorts = out["enrol_year"].unique().drop_nulls().to_list()
    if cohorts:
        gap = pl.DataFrame(
            {
                "enrol_year": cohorts,
                "age_band": [AGE_LABELS[-1]] * len(cohorts),
                "origin": ["not observable"] * len(cohorts),
                "n_cda": [None] * len(cohorts),
                "cum_paid": [None] * len(cohorts),
                "enrolled": [None] * len(cohorts),
                "n_cnpj": [None] * len(cohorts),
                "cumulative": [None] * len(cohorts),
            }
        )
        out = pl.concat([out, gap], how="diagonal_relaxed")
    return assert_public(out), n_sup


def recovery_age_page(
    panel: pl.DataFrame | pl.LazyFrame, firms: pl.DataFrame
) -> dict[str, Any]:
    """Age-band tables. The panel stays lazy; each table is a streaming aggregate."""
    _BUILDS["age_base"] += 1
    if _is_lazy(panel):
        return {
            "annual": annual_recovery(panel, firms),
            "mean": mean_recovery(panel, firms),
            "cumulative": cumulative_by_band(panel),
            "status": recovery_by_status(panel, firms),
        }
    base = _age_frame(panel, firms)
    if isinstance(base, pl.DataFrame):
        base = base.lazy()
    return {
        "annual": annual_recovery(panel, firms, base=base),
        "mean": mean_recovery(panel, firms, base=base),
        "cumulative": cumulative_by_band(panel, base=base),
        "status": recovery_by_status(panel, firms, base=base),
    }


def _cnae_division(expr: pl.Expr) -> pl.Expr:
    digits = expr.cast(pl.Utf8).str.replace_all(r"\D", "")
    return (
        pl.when(digits.is_null() | (digits.str.len_chars() == 0))
        .then(pl.lit(None))
        .otherwise(digits.str.slice(0, 2))
    )


def _attach_flags(df: pl.DataFrame) -> pl.DataFrame:
    """Dated instalment and protest flags. The protest status is not a column."""
    return (
        df.with_columns(
            pl.col("match_nivel").fill_null("nao_encontrado"),
            _status_expr(),
        )
        .with_columns(
            pl.col("stock").log().alias("log_stock"),
            pl.col("year").cast(pl.Utf8),
            (
                pl.col("first_request").is_not_null()
                & (pl.col("first_request").dt.year() < pl.col("year"))
            )
            .cast(pl.Int8)
            .alias("instalment_requested_before"),
            (
                pl.col("first_break").is_not_null()
                & (pl.col("first_break").dt.year() < pl.col("year"))
            )
            .cast(pl.Int8)
            .alias("instalment_broken_before"),
            pl.when(
                pl.col("first_protest").is_not_null()
                & (pl.col("first_protest").dt.year() < pl.col("year"))
            )
            .then(pl.lit("protested before the year"))
            .when(
                pl.col("first_protest").is_null()
                & (pl.col("n_null_protest").fill_null(0) > 0)
            )
            .then(pl.lit("date missing"))
            .otherwise(pl.lit("not protested"))
            .alias("protest_before"),
            _cnae_division(pl.col("cnae_divisao")).alias("cnae_division"),
            pl.col("porte")
            .cast(pl.Utf8)
            .replace_strict(PORTE_LABEL, default="other code")
            .fill_null("unknown")
            .alias("porte_label"),
        )
        .select(
            "id_debito",
            "year",
            "cnpj",
            "cnpj_root",
            "paid_flag",
            "log_stock",
            "age",
            "rate_l1",
            "origin",
            "status",
            "cnae_division",
            "porte_label",
            "instalment_requested_before",
            "instalment_broken_before",
            "protest_before",
        )
    )


def _logit_side_sql(
    instalments: pl.DataFrame | pl.LazyFrame,
    protests: pl.DataFrame | pl.LazyFrame,
    ids: pl.DataFrame,
) -> dict[str, pl.DataFrame] | None:
    """One date per sampled debt. The protest and request tables are not collected."""
    con = _duck()
    try:
        inst = _sql_relation(con, instalments, "instalments")
        prot = _sql_relation(con, protests, "protests")
        if inst is None or prot is None:
            return None
        con.register("sample_ids", ids.select("id_debito"))
        req = con.execute(
            f"""
            SELECT CAST(e.id_debito AS BIGINT) AS id_debito,
                   MIN(e.data_solicitacao) AS first_request
            FROM {inst} e
            INNER JOIN sample_ids i ON {_id_eq("e", "i")}
            WHERE {_window_sql("e.data_solicitacao")}
            GROUP BY 1
            """
        ).pl()
        brk = con.execute(
            f"""
            SELECT CAST(e.id_debito AS BIGINT) AS id_debito,
                   MIN(e.data_rompimento) AS first_break
            FROM {inst} e
            INNER JOIN sample_ids i ON {_id_eq("e", "i")}
            WHERE {_window_sql("e.data_rompimento")}
            GROUP BY 1
            """
        ).pl()
        protest = con.execute(
            f"""
            SELECT CAST(e.id_debito AS BIGINT) AS id_debito,
                   MIN(CASE WHEN {_window_sql("e.data_protesto")}
                            THEN e.data_protesto END) AS first_protest,
                   CAST(SUM(CASE WHEN e.data_protesto IS NULL THEN 1 ELSE 0 END)
                        AS BIGINT) AS n_null_protest
            FROM {prot} e
            INNER JOIN sample_ids i ON {_id_eq("e", "i")}
            GROUP BY 1
            """
        ).pl()
    finally:
        con.close()
    _SQL_RUNS["logit"] += 1
    return {"req": req, "brk": brk, "prot": protest}


def _stratum_allocation(counts: pl.DataFrame, sample_n: int) -> pl.DataFrame:
    """Largest-remainder allocation. A stratum is not given more rows than it has."""
    total = int(counts["n"].sum())
    rows = counts.to_dicts()
    if total <= sample_n:
        for row in rows:
            row["n_keep"] = int(row["n"])
        return pl.DataFrame(rows)
    for row in rows:
        row["n_keep"] = min(int(row["n"]), int(row["n"]) * sample_n // total)
        row["_frac"] = row["n"] * sample_n / total - row["n_keep"]
    short = sample_n - sum(int(row["n_keep"]) for row in rows)
    for row in sorted(
        rows,
        key=lambda item: (
            -item["_frac"],
            item.get("year") or 0,
            str(item.get("_origin_key")),
            item.get("_paid_key") if item.get("_paid_key") is not None else -1,
        ),
    ):
        if short <= 0:
            break
        room = int(row["n"]) - int(row["n_keep"])
        if room <= 0:
            continue
        take = min(room, short)
        row["n_keep"] += take
        short -= take
    return pl.DataFrame(rows).drop("_frac")


def estimation_frame(
    panel: pl.DataFrame | pl.LazyFrame,
    firms: pl.DataFrame,
    instalments: pl.DataFrame | pl.LazyFrame,
    protests: pl.DataFrame | pl.LazyFrame,
    params: PgeParams,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Design of the nested logits. The frame is an input to the fit, not a page table."""
    reject_person_columns(firms)
    eligible = _lazy(panel).filter(
        (pl.col("year") >= FIRST_YEAR)
        & (pl.col("year") <= LAST_YEAR)
        & (pl.col("stock") > 0)
        & pl.col("rate_l1").is_not_null()
        & pl.col("age").is_not_null()
    )
    # One row per debt for the dated flags, then join. The raw tables stay on disk.
    first_req = (
        _lazy(instalments)
        .filter(_plausible("data_solicitacao"))
        .group_by("id_debito")
        .agg(pl.col("data_solicitacao").min().alias("first_request"))
    )
    first_break = (
        _lazy(instalments)
        .filter(_plausible("data_rompimento"))
        .group_by("id_debito")
        .agg(pl.col("data_rompimento").min().alias("first_break"))
    )
    protest_facts = (
        _lazy(protests)
        .group_by("id_debito")
        .agg(
            pl.col("data_protesto")
            .filter(_plausible("data_protesto"))
            .min()
            .alias("first_protest"),
            pl.col("data_protesto").null_count().alias("n_null_protest"),
        )
    )
    firm = firms.lazy().select(
        "cnpj",
        "match_nivel",
        pl.col("situacao_cadastral").alias("registry_code"),
        "cnae_divisao",
        "porte",
    )

    def _features(rows: pl.LazyFrame) -> pl.LazyFrame:
        return (
            rows.join(firm, on="cnpj", how="left")
            .join(first_req, on="id_debito", how="left")
            .join(first_break, on="id_debito", how="left")
            .join(protest_facts, on="id_debito", how="left")
        )

    trunc_expr = (
        pl.col("cnae_divisao").cast(pl.Utf8).str.replace_all(r"\D", "").str.len_chars()
        > 2
    ).fill_null(False)
    counted = _collect(
        eligible.join(
            firm.select("cnpj", "cnae_divisao"), on="cnpj", how="left"
        ).select(
            pl.len().alias("n"),
            trunc_expr.sum().alias("n_cnae_truncated"),
        )
    )
    n_before = int(counted["n"][0])
    n_trunc = int(counted["n_cnae_truncated"][0] or 0)
    rows = eligible
    stratified = n_before > params.sample_n
    if stratified:
        # Strata are year, origin and the outcome. The draw stays on disk until collect.
        keyed = eligible.with_columns(
            pl.col("origin").fill_null("__missing_origin__").alias("_origin_key"),
            pl.col("paid_flag").fill_null(-1).alias("_paid_key"),
        )
        counts = _collect(
            keyed.group_by("year", "_origin_key", "_paid_key").agg(pl.len().alias("n"))
        )
        alloc = _stratum_allocation(counts, params.sample_n)
        rows = (
            keyed.join(
                alloc.lazy().select("year", "_origin_key", "_paid_key", "n", "n_keep"),
                on=["year", "_origin_key", "_paid_key"],
                how="inner",
            )
            .filter(
                (
                    pl.col("id_debito").hash(seed=params.seed)
                    % pl.col("n").cast(pl.UInt64)
                )
                < pl.col("n_keep").cast(pl.UInt64)
            )
            .drop("_origin_key", "_paid_key", "n", "n_keep")
        )
    collected = None
    if _is_lazy(instalments) or _is_lazy(protests):
        base = _collect(
            rows.join(firm, on="cnpj", how="left").select(
                "id_debito",
                "year",
                "cnpj",
                "cnpj_root",
                "paid_flag",
                "stock",
                "age",
                "rate_l1",
                "origin",
                "match_nivel",
                "registry_code",
                "cnae_divisao",
                "porte",
            )
        )
        side = _logit_side_sql(instalments, protests, base.select("id_debito").unique())
        if side is not None:
            collected = _attach_flags(
                base.join(side["req"], on="id_debito", how="left")
                .join(side["brk"], on="id_debito", how="left")
                .join(side["prot"], on="id_debito", how="left")
            )
    if collected is None:
        collected = _attach_flags(_collect(_features(rows)))
    notes: dict[str, Any] = {
        "n_before_sample": n_before,
        "n_cnae_truncated": n_trunc,
        "sample_n": params.sample_n,
        "seed": params.seed,
        "formula_m1": M1_FORMULA,
        "stratified": stratified,
        "strata": "year, origin, paid_flag",
    }
    if collected.height > params.sample_n:
        collected = collected.sort(["id_debito", "year"]).sample(
            n=params.sample_n, seed=params.seed
        )
    notes["n_sampled"] = collected.height
    collected, n_cnae = _pool_rare(collected, "cnae_division")
    collected, n_status = _pool_rare(collected, "status")
    collected, n_porte = _pool_rare(collected, "porte_label")
    notes["n_cells_suppressed"] = n_cnae + n_status + n_porte
    # A division that was pooled and is still under the floor is null. Drop those
    # rows from every nested model so the sample is the same.
    before = collected.height
    collected = collected.filter(
        pl.col("cnae_division").is_not_null()
        & pl.col("status").is_not_null()
        & pl.col("porte_label").is_not_null()
    )
    notes["n_dropped_blank"] = before - collected.height
    notes["n_estimated"] = collected.height
    if "status_protesto" in collected.columns:
        raise ValueError("protest status is not a regressor")
    return collected.to_pandas(), notes


def _pool_rare(df: pl.DataFrame, col: str) -> tuple[pl.DataFrame, int]:
    """Pool a category with fewer than 5 CNPJs. Blank the pool when it stays under 5."""
    sizes = df.group_by(col).agg(pl.col("cnpj").n_unique().alias("n"))
    small = sizes.filter((pl.col("n") > 0) & (pl.col("n") < MIN_CNPJ))
    n_sup = small.height
    labels = small[col].to_list()
    out = df.with_columns(
        pl.when(pl.col(col).is_in(labels))
        .then(pl.lit(SUPPRESSED))
        .otherwise(pl.col(col))
        .alias(col)
    )
    pooled_n = out.filter(pl.col(col) == SUPPRESSED)["cnpj"].n_unique()
    if pooled_n < MIN_CNPJ:
        out = out.with_columns(
            pl.when(pl.col(col) == SUPPRESSED)
            .then(pl.lit(None))
            .otherwise(pl.col(col))
            .alias(col)
        )
    return out, n_sup


def nested_formulas(frame: pd.DataFrame) -> list[tuple[str, str]]:
    """M1 is the payment-section logit. Later models add one block at a time."""
    origin_ref = "declared" if (frame["origin"] == "declared").any() else None
    status_ref = "active" if (frame["status"] == "active").any() else None
    porte_ref = "other sizes" if (frame["porte_label"] == "other sizes").any() else None
    protest_ref = (
        "not protested" if (frame["protest_before"] == "not protested").any() else None
    )

    def cat(name: str, ref: str | None) -> str:
        if ref is None:
            return f"C({name})"
        return f'C({name}, Treatment(reference="{ref}"))'

    blocks = [
        ("M1", M1_FORMULA),
        ("M2", M1_FORMULA + " + " + cat("origin", origin_ref)),
        (
            "M3",
            M1_FORMULA
            + " + "
            + cat("origin", origin_ref)
            + " + "
            + cat("status", status_ref),
        ),
        (
            "M4",
            M1_FORMULA
            + " + "
            + cat("origin", origin_ref)
            + " + "
            + cat("status", status_ref)
            + " + C(cnae_division)",
        ),
        (
            "M5",
            M1_FORMULA
            + " + "
            + cat("origin", origin_ref)
            + " + "
            + cat("status", status_ref)
            + " + C(cnae_division) + "
            + cat("porte_label", porte_ref),
        ),
        (
            "M6",
            M1_FORMULA
            + " + "
            + cat("origin", origin_ref)
            + " + "
            + cat("status", status_ref)
            + " + C(cnae_division) + "
            + cat("porte_label", porte_ref)
            + " + instalment_requested_before + instalment_broken_before",
        ),
        (
            "M7",
            M1_FORMULA
            + " + "
            + cat("origin", origin_ref)
            + " + "
            + cat("status", status_ref)
            + " + C(cnae_division) + "
            + cat("porte_label", porte_ref)
            + " + instalment_requested_before + instalment_broken_before + "
            + cat("protest_before", protest_ref),
        ),
    ]
    return blocks


def fit_nested(
    frame: pd.DataFrame, params: PgeParams
) -> tuple[pd.DataFrame, pd.DataFrame, str]:
    """HC1 coefficients. Clustered standard errors reuse the same fit.

    L-BFGS is tried first. Newton is the fallback. The cluster column is a
    sandwich on the fitted coefficients, not a second optimisation.
    """
    import statsmodels.formula.api as smf

    formulas = nested_formulas(frame)
    coef_rows: list[dict[str, Any]] = []
    fit_rows: list[dict[str, Any]] = []
    solver_used = "lbfgs"

    def _fit(formula: str):
        nonlocal solver_used
        model = smf.logit(formula, data=frame)
        last: Exception | None = None
        for method in ("lbfgs", "bfgs", None):
            kwargs: dict[str, Any] = {"disp": False, "maxiter": 200, "cov_type": "HC1"}
            if method is not None:
                kwargs["method"] = method
            try:
                fitted = model.fit(**kwargs)
            except (np.linalg.LinAlgError, ValueError) as exc:
                last = exc
                continue
            solver_used = method or "newton"
            return fitted
        raise last if last is not None else RuntimeError("logit did not fit")

    for name, formula in formulas:
        res = _fit(formula)
        index = res.params.index
        se_h = pd.Series(np.asarray(res.bse).ravel(), index=index)
        p_h = pd.Series(np.asarray(res.pvalues).ravel(), index=index)
        ci = np.asarray(res.conf_int())
        se_c = pd.Series(np.nan, index=index)
        p_c = pd.Series(np.nan, index=index)
        cluster_ok = False
        if params.cluster:
            try:
                groups = frame.loc[res.model.data.row_labels, "cnpj_root"]
                res._results._get_robustcov_results(cov_type="cluster", groups=groups)
                res._results._cache.clear()
                se_c = pd.Series(np.asarray(res.bse).ravel(), index=index)
                p_c = pd.Series(np.asarray(res.pvalues).ravel(), index=index)
                cluster_ok = True
            except (ValueError, np.linalg.LinAlgError, KeyError, IndexError):
                cluster_ok = False
        place = {term: i for i, term in enumerate(index)}
        n_year = int(sum(str(term).startswith("C(year)") for term in index))
        fit_rows.append(
            {
                "model": name,
                "pseudo_r2": float(res.prsquared),
                "aic": float(res.aic),
                "bic": float(res.bic),
                "n": int(res.nobs),
                "n_year_terms": n_year,
                "converged": bool(res.mle_retvals.get("converged", True)),
                "cluster_ok": cluster_ok,
            }
        )
        for term in index:
            if str(term).startswith("C(year)"):
                continue
            coef_rows.append(
                {
                    "model": name,
                    "term": term,
                    "coef": float(res.params[term]),
                    "se_hc1": float(se_h[term]),
                    "p_hc1": float(p_h[term]),
                    "stars": stars(float(p_h[term])),
                    "ci_low": float(ci[place[term], 0]),
                    "ci_high": float(ci[place[term], 1]),
                    "odds_ratio": _safe_exp(float(res.params[term])),
                    "or_low": _safe_exp(float(ci[place[term], 0])),
                    "or_high": _safe_exp(float(ci[place[term], 1])),
                    "se_cluster": float(se_c.get(term, np.nan)),
                    "p_cluster": float(p_c.get(term, np.nan)),
                    "stars_cluster": stars(float(p_c.get(term, np.nan))),
                }
            )
    coef = assert_public(pd.DataFrame(coef_rows))
    fit = assert_public(pd.DataFrame(fit_rows))
    return coef, fit, solver_used


def _fit_cache_dir() -> Path:
    """Coefficient cache. The design matrix is not written."""
    import tempfile

    base = os.environ.get("CEMEPI_CACHE")
    root = (
        Path(base) / "results" / "imp23-fit"
        if base
        else Path(tempfile.gettempdir()) / "imp23-fit"
    )
    root.mkdir(parents=True, exist_ok=True)
    return root


def _fit_cache_key(frame: pd.DataFrame, params: PgeParams) -> str:
    import hashlib

    digest = hashlib.sha256()
    digest.update(M1_FORMULA.encode())
    digest.update(str(params.sample_n).encode())
    digest.update(str(params.seed).encode())
    digest.update(str(params.cluster).encode())
    ordered = frame.sort_values(["id_debito", "year"], kind="mergesort")
    for column in ordered.columns:
        digest.update(column.encode())
        hashed = pd.util.hash_pandas_object(ordered[column], index=False).to_numpy()
        digest.update(np.ascontiguousarray(hashed).tobytes())
    return digest.hexdigest()[:32]


def _read_fit_cache(
    key: str,
) -> tuple[pd.DataFrame, pd.DataFrame, str] | None:
    root = _fit_cache_dir()
    coef_path = root / f"{key}-coef.parquet"
    fit_path = root / f"{key}-fit.parquet"
    solver_path = root / f"{key}-solver.txt"
    if not (coef_path.exists() and fit_path.exists() and solver_path.exists()):
        return None
    return (
        pd.read_parquet(coef_path),
        pd.read_parquet(fit_path),
        solver_path.read_text(encoding="utf-8").strip(),
    )


def _write_fit_cache(
    key: str, coef: pd.DataFrame, fit: pd.DataFrame, solver: str
) -> None:
    root = _fit_cache_dir()
    coef.to_parquet(root / f"{key}-coef.parquet", index=False)
    fit.to_parquet(root / f"{key}-fit.parquet", index=False)
    (root / f"{key}-solver.txt").write_text(solver, encoding="utf-8")


def fit_payment_models(
    panel: pl.DataFrame | pl.LazyFrame,
    firms: pl.DataFrame,
    instalments: pl.DataFrame | pl.LazyFrame,
    protests: pl.DataFrame | pl.LazyFrame,
    params: PgeParams,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    import time

    started = time.perf_counter()
    frame, notes = estimation_frame(panel, firms, instalments, protests, params)
    # The micro frame stays inside this function. Only the coefficient tables leave.
    key = _fit_cache_key(frame, params)
    cached = _read_fit_cache(key)
    if cached is None:
        coef, fit, solver = fit_nested(frame, params)
        _write_fit_cache(key, coef, fit, solver)
        notes["fit_cache"] = "miss"
    else:
        coef, fit, solver = cached
        notes["fit_cache"] = "hit"
    notes["formula_m1"] = M1_FORMULA
    notes["solver"] = solver
    notes["fit_seconds"] = round(time.perf_counter() - started, 3)
    return coef, fit, notes


def fig_lines(
    df: pl.DataFrame,
    x: str,
    y: str,
    group: str | None = None,
    *,
    x_title: str = "",
    y_title: str = "",
) -> go.Figure:
    fig = go.Figure()
    if group is None:
        pieces = [(None, df)]
    else:
        pieces = [
            (str(k[0]), g)
            for k, g in df.sort(group).group_by(group, maintain_order=True)
        ]
    for i, (name, g) in enumerate(pieces):
        g = g.sort(x)
        fig.add_trace(
            go.Scatter(
                x=g[x].to_list(),
                y=g[y].to_list(),
                mode="lines+markers",
                name=name or y,
                line={"color": PALETTE[i % len(PALETTE)], "width": 1.6},
                marker={"size": 5},
            )
        )
    fig.update_layout(
        template=None,
        font={
            "family": "Montserrat, Arial, sans-serif",
            "size": 12,
            "color": "#154C56",
        },
        paper_bgcolor="white",
        plot_bgcolor="white",
        xaxis_title=x_title,
        yaxis_title=y_title,
        legend={"orientation": "h", "y": 1.12, "x": 0},
    )
    _reject_webgl(fig)
    return fig


def fig_bars(
    df: pl.DataFrame,
    cat: str,
    val: str,
    *,
    x_title: str = "",
    y_title: str = "",
) -> go.Figure:
    fig = go.Figure(
        go.Bar(
            x=df[cat].to_list(),
            y=df[val].to_list(),
            marker_color="#154C56",
        )
    )
    fig.update_layout(
        template=None,
        font={
            "family": "Montserrat, Arial, sans-serif",
            "size": 12,
            "color": "#154C56",
        },
        paper_bgcolor="white",
        plot_bgcolor="white",
        xaxis_title=x_title,
        yaxis_title=y_title,
    )
    _reject_webgl(fig)
    return fig


def fig_heatmap(
    df: pl.DataFrame,
    row: str,
    col: str,
    val: str,
    *,
    x_title: str = "",
    y_title: str = "",
) -> go.Figure:
    wide = df.pivot(on=col, index=row, values=val, aggregate_function="first").sort(row)
    cols = [c for c in wide.columns if c != row]
    fig = go.Figure(
        go.Heatmap(
            z=wide.select(cols).to_numpy(),
            x=cols,
            y=wide[row].to_list(),
            colorscale=[[0, "#FFF4E5"], [1, "#154C56"]],
            colorbar={"title": val},
        )
    )
    fig.update_layout(
        template=None,
        font={
            "family": "Montserrat, Arial, sans-serif",
            "size": 12,
            "color": "#154C56",
        },
        paper_bgcolor="white",
        plot_bgcolor="white",
        xaxis_title=x_title,
        yaxis_title=y_title,
    )
    _reject_webgl(fig)
    return fig


def _gl_tokens() -> tuple[str, ...]:
    # Split so the source does not contain a WebGL trace name.
    return ("scatter" + "gl", "heatmap" + "gl", "scatterpolar" + "gl", "web" + "gl")


def _reject_webgl(fig: go.Figure) -> None:
    blob = fig.to_json().lower()
    for token in _gl_tokens():
        if token in blob:
            raise ValueError("figure uses a WebGL trace")


def uses_webgl(fig: go.Figure) -> bool:
    blob = fig.to_json().lower()
    return any(token in blob for token in _gl_tokens())


# --- synthetic generator (no file, invalid CNPJ check digits) -----------------


def _cnpj_valid(digits: str) -> bool:
    if len(digits) != 14 or not digits.isdigit() or digits == digits[0] * 14:
        return False
    nums = [int(c) for c in digits]
    weights = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    for pos in (12, 13):
        total = sum(n * w for n, w in zip(nums[:pos], weights, strict=True))
        rest = total % 11
        dv = 0 if rest < 2 else 11 - rest
        if dv != nums[pos]:
            return False
        weights = [6, *weights]
    return True


def fake_cnpj(root8: str, branch4: str) -> str:
    stem = f"{root8}{branch4}"
    for d in range(100):
        cand = f"{stem}{d:02d}"
        if not _cnpj_valid(cand):
            return cand
    raise RuntimeError("could not build an invalid CNPJ")


def _root(pattern: int, k: int) -> str:
    return f"{pattern:02d}{k:02d}0000"


def synthetic_bundle(seed: int = SEED) -> dict[str, Any]:
    """Small legal-entity panel that exercises every rule branch and the suppression floor."""
    rng = np.random.default_rng(seed)
    clones = 5
    # pattern, origin, enrol, registry, match, cnae, porte, situacao,
    # paid_year -> amount (share of stock 1000), requests, protest
    patterns: list[dict[str, Any]] = [
        {
            "pid": 1,
            "origin": "declared",
            "enrol": date(2018, 6, 1),
            "code": "02",
            "match": "estabelecimento",
            "cnae": "46",
            "porte": "05",
            "situacao": "Suspenso",
            "paid": {2019: 0},
            "requests": [
                {
                    "date": date(2019, 3, 1),
                    "status": "Em andamento",
                    "qtd": 12,
                    "break_date": None,
                }
            ],
            "protest": None,
        },
        {
            "pid": 2,
            "origin": "declared",
            "enrol": date(2019, 2, 1),
            "code": "02",
            "match": "estabelecimento",
            "cnae": "46",
            "porte": "05",
            "situacao": "Inscrito",
            "paid": {},
            "requests": [
                {
                    "date": date(2019, 4, 1),
                    "status": "Em andamento",
                    "qtd": 6,
                    "break_date": None,
                }
            ],
            "protest": None,
        },
        {
            "pid": 3,
            "origin": "assessed",
            "enrol": date(2014, 6, 1),
            "code": "02",
            "match": "estabelecimento",
            "cnae": "47",
            "porte": "01",
            "situacao": "Inscrito",
            "paid": {2015: 500, 2018: 200, 2020: 100},
            "requests": [],
            "protest": {"date": date(2019, 5, 1), "status": "Protestado"},
        },
        {
            "pid": 4,
            "origin": "declared",
            "enrol": date(2017, 5, 1),
            "code": "08",
            "match": "estabelecimento",
            "cnae": "46",
            "porte": "03",
            "situacao": "Inscrito",
            "paid": {2019: 150},
            "requests": [],
            "protest": {"date": None, "status": "Enviado"},
        },
        {
            "pid": 5,
            "origin": "declared",
            "enrol": date(2018, 8, 1),
            "code": "02",
            "match": "estabelecimento",
            "cnae": "47",
            "porte": "01",
            "situacao": "Inscrito",
            "paid": {2019: 50},
            "requests": [],
            "protest": None,
        },
        {
            "pid": 6,
            "origin": "assessed",
            "enrol": date(2018, 1, 1),
            "code": "04",
            "match": "estabelecimento",
            "cnae": "47",
            "porte": "01",
            "situacao": "Inscrito",
            "paid": {},
            "requests": [],
            "protest": None,
        },
        {
            "pid": 7,
            "origin": "declared",
            "enrol": date(2010, 6, 1),
            "code": "02",
            "match": "estabelecimento",
            "cnae": "46",
            "porte": "05",
            "situacao": "Inscrito",
            "paid": {},
            "requests": [],
            "protest": None,
        },
        {
            "pid": 8,
            "origin": "declared",
            "enrol": date(2018, 1, 1),
            "code": None,
            "match": "nao_encontrado",
            "cnae": None,
            "porte": None,
            "situacao": "Inscrito",
            "paid": {},
            "requests": [],
            "protest": {"date": date(2019, 1, 1), "status": "Rare status"},
        },
        {
            "pid": 9,
            "origin": "assessed",
            "enrol": date(2018, 1, 1),
            "code": None,
            "match": "raiz",
            "cnae": None,
            "porte": None,
            "situacao": "Inscrito",
            "paid": {},
            "requests": [],
            "protest": None,
        },
        {
            "pid": 14,
            "origin": "declared",
            "enrol": date(2015, 12, 31),
            "code": "02",
            "match": "estabelecimento",
            "cnae": "46",
            "porte": "05",
            "situacao": "Inscrito",
            "paid": {},
            "requests": [],
            "protest": None,
        },
        {
            "pid": 15,
            "origin": "declared",
            "enrol": date(2015, 12, 30),
            "code": "02",
            "match": "estabelecimento",
            "cnae": "46",
            "porte": "05",
            "situacao": "Inscrito",
            "paid": {},
            "requests": [],
            "protest": None,
        },
        {
            "pid": 16,
            "origin": "assessed",
            "enrol": date(2016, 3, 1),
            "code": "02",
            "match": "estabelecimento",
            "cnae": "47",
            "porte": "01",
            "situacao": "Inscrito",
            "paid": {2017: 200},
            "requests": [],
            "protest": None,
        },
        {
            "pid": 17,
            "origin": "declared",
            "enrol": date(2016, 4, 1),
            "code": "02",
            "match": "estabelecimento",
            "cnae": "46",
            "porte": "03",
            "situacao": "Inscrito",
            "paid": {2018: 200},
            "requests": [],
            "protest": None,
        },
        {
            "pid": 20,
            "origin": "assessed",
            "enrol": date(2017, 7, 1),
            "code": "02",
            "match": "estabelecimento",
            "cnae": "47",
            "porte": "01",
            "situacao": "Inscrito",
            "paid": {2019: 100, 2021: 80},
            "requests": [
                {
                    "date": date(2018, 1, 1),
                    "status": "Rompido pelo contribuinte",
                    "qtd": 24,
                    "break_date": date(2019, 6, 1),
                }
            ],
            "protest": None,
        },
    ]
    firms: list[dict[str, Any]] = []
    panel_rows: list[dict[str, Any]] = []
    requests: list[dict[str, Any]] = []
    protests: list[dict[str, Any]] = []
    next_id = 1

    def add_cda(
        pid: int, k: int, slot: int, spec: dict[str, Any], stock: float = 1000.0
    ) -> int:
        nonlocal next_id
        cid = next_id
        next_id += 1
        root = _root(pid, k)
        cnpj = fake_cnpj(root, f"{slot:04d}")
        firms.append(
            {
                "cnpj": cnpj,
                "match_nivel": spec["match"],
                "cnae_divisao": spec["cnae"],
                "cnae_secao": "G" if spec["cnae"] in {"46", "47"} else None,
                "situacao_cadastral": spec["code"],
                "porte": spec["porte"],
            }
        )
        paid_map = dict(spec["paid"])
        years = list(range(max(spec["enrol"].year, FIRST_YEAR - 1), LAST_YEAR + 1))
        prev_rate = None
        prev_year = None
        for year in years:
            paid = float(paid_map.get(year, 0.0))
            rate = paid / stock
            rate_l1 = prev_rate if prev_year == year - 1 else None
            age = (date(year, 12, 31) - spec["enrol"]).days / 365.25
            panel_rows.append(
                {
                    "id_debito": cid,
                    "year": year,
                    "cnpj": cnpj,
                    "cnpj_root": root,
                    "origin": spec["origin"],
                    "enrol_date": spec["enrol"],
                    "enrol_year": spec["enrol"].year,
                    "situacao": spec["situacao"],
                    "stock": stock,
                    "paid": paid,
                    "paid_flag": int(paid > 0),
                    "rate": rate,
                    "rate_l1": rate_l1,
                    "age": age,
                    "present_in_december": True,
                    "enrolled_value": stock,
                }
            )
            prev_rate, prev_year = rate, year
        for req in spec["requests"]:
            requests.append(
                {
                    "id_solicitacao": len(requests) + 1,
                    "id_debito": cid,
                    "data_solicitacao": req["date"],
                    "status_parcelamento": req["status"],
                    "data_rompimento": req["break_date"],
                    "qtd_parcelas": req["qtd"],
                }
            )
        if spec["protest"] is not None:
            protests.append(
                {
                    "id_debito": cid,
                    "status_protesto": spec["protest"]["status"],
                    "data_protesto": spec["protest"]["date"],
                    "valor_protestado": 100.0,
                }
            )
        return cid

    for spec in patterns:
        # The rare protest status stays on a single CNPJ so suppression has work to do.
        n = 1 if spec["pid"] == 8 else clones
        for k in range(n):
            add_cda(spec["pid"], k, 0, spec)

    # Two CDAs, one root, share below 10 percent: 50 + 50 over 2000.
    for k in range(clones):
        root = _root(10, k)
        for slot, paid in ((0, {2019: 50.0}), (1, {2019: 50.0})):
            add_cda(
                10,
                k,
                slot,
                {
                    "origin": "declared",
                    "enrol": date(2019, 1, 1),
                    "code": "02",
                    "match": "estabelecimento",
                    "cnae": "46",
                    "porte": "05",
                    "situacao": "Inscrito",
                    "paid": paid,
                    "requests": [],
                    "protest": None,
                },
            )
            firms[-1]["cnpj"] = fake_cnpj(root, f"{slot:04d}")
            # The helper used _root(10, k) already. Rewrite the last CDA's root to be sure.
            last_id = panel_rows[-1]["id_debito"]
            for row in panel_rows:
                if row["id_debito"] == last_id:
                    row["cnpj_root"] = root
                    row["cnpj"] = firms[-1]["cnpj"]

    # Two CDAs, one root, share 15 percent, and one of them paid nothing.
    for k in range(clones):
        root = _root(12, k)
        for slot, paid in ((0, {2018: 300.0}), (1, {})):
            add_cda(
                12,
                k,
                slot,
                {
                    "origin": "assessed",
                    "enrol": date(2014, 1, 1),
                    "code": "02",
                    "match": "estabelecimento",
                    "cnae": "47",
                    "porte": "01",
                    "situacao": "Inscrito",
                    "paid": paid,
                    "requests": [],
                    "protest": None,
                },
            )
            firms[-1]["cnpj"] = fake_cnpj(root, f"{slot:04d}")
            last_id = panel_rows[-1]["id_debito"]
            for row in panel_rows:
                if row["id_debito"] == last_id:
                    row["cnpj_root"] = root
                    row["cnpj"] = firms[-1]["cnpj"]

    # Two firms in a division of their own, so the division is pooled.
    for k in range(2):
        add_cda(
            99,
            k,
            0,
            {
                "origin": "declared",
                "enrol": date(2017, 3, 1),
                "code": "02",
                "match": "estabelecimento",
                "cnae": "99",
                "porte": "05",
                "situacao": "Inscrito",
                "paid": {2019: 40},
                "requests": [],
                "protest": None,
            },
            stock=500.0,
        )

    # Ordinary firms: both outcomes in every year, for the logit.
    for k in range(24):
        origin = "declared" if k % 2 == 0 else "assessed"
        cnae = "46" if (k // 2) % 2 == 0 else "47"
        porte = ("05", "03", "01")[k % 3]
        spec = {
            "origin": origin,
            "enrol": date(2017, 3, 1),
            "code": "02",
            "match": "estabelecimento",
            "cnae": cnae,
            "porte": porte,
            "situacao": "Inscrito",
            "paid": {},
            "requests": [],
            "protest": None,
        }
        add_cda(50, k, 0, spec, stock=500.0)
        last_id = panel_rows[-1]["id_debito"]
        prev_rate = None
        prev_year = None
        for row in panel_rows:
            if row["id_debito"] != last_id:
                continue
            if row["year"] < FIRST_YEAR:
                continue
            paid = 25.0 if rng.random() < 0.30 else 0.0
            row["paid"] = paid
            row["paid_flag"] = int(paid > 0)
            row["rate"] = paid / row["stock"]
            row["rate_l1"] = prev_rate if prev_year == row["year"] - 1 else None
            prev_rate, prev_year = row["rate"], row["year"]

    # One row after the window, so the filters have something to drop.
    extra = dict(panel_rows[0])
    extra["year"] = LAST_YEAR + 1
    extra["paid"] = 1.0
    panel_rows.append(extra)

    # Impossible break dates. They sit on pattern 2 (already an open request).
    host = next(
        r["id_debito"]
        for r in panel_rows
        if r["year"] == 2019 and r["situacao"] == "Suspenso"
    )
    # Use a pattern-2 id instead: first request with status Em andamento and qtd 6.
    host_open = requests[0]["id_debito"]
    for bad, qtd, status in (
        (date(502, 3, 1), 24, "Rompido pelo contribuinte"),
        (date(LAST_YEAR + 1, 2, 2), 36, "Rompido pelo contribuinte"),
        (date(FIRST_YEAR - 1, 5, 5), 3, "Rompido pelo contribuinte"),
    ):
        requests.append(
            {
                "id_solicitacao": len(requests) + 1,
                "id_debito": host_open,
                "data_solicitacao": date(2018, 1, 1),
                "status_parcelamento": status,
                "data_rompimento": bad,
                "qtd_parcelas": qtd,
            }
        )
    del host  # the suspended host is not the carrier of the bad dates

    panel = pl.DataFrame(panel_rows)
    # rate_l1 may be null; polars needs a float dtype.
    panel = panel.with_columns(pl.col("rate_l1").cast(pl.Float64))
    payments = panel.filter(pl.col("paid") > 0).select(
        "id_debito",
        pl.date(pl.col("year"), 6, 15).alias("pay_date"),
        pl.col("paid").alias("amount"),
    )
    return {
        "panel": panel,
        "firms": pl.DataFrame(firms).unique(subset=["cnpj"]),
        "instalments": pl.DataFrame(requests),
        "protests": pl.DataFrame(protests),
        "payments": payments,
        "synthetic": True,
    }


def page_setup(
    include: tuple[str, ...] | None = None,
) -> tuple[dict[str, Any], PgeParams]:
    """Synthetic bundle when IMP23_SYNTHETIC=1. Otherwise the read-only extract.

    ``include`` names the pieces a page actually uses, so a page does not read the
    protest table when it only needs the panel.
    """
    params = PgeParams()
    if os.environ.get("IMP23_SYNTHETIC") == "1":
        bundle = synthetic_bundle(params.seed)
        bundle["synthetic"] = True
        return bundle, params
    bundle = load_observed_bundle(include)
    bundle["synthetic"] = False
    return bundle, params


def _scan_sql(sql: str, normalise, step: str) -> pl.LazyFrame:
    """Stream a read-only query to parquet. The raw table is not collected into memory."""
    import hashlib

    import _lab_base as B

    digest = hashlib.sha256(sql.encode()).hexdigest()[:16]
    path = B.CACHE_DIR / f"{step}_{digest}.parquet"
    if not path.exists():
        tmp = path.with_suffix(".tmp.parquet")
        con = B.duck()
        try:
            con.execute(
                "COPY ("
                + sql
                + f") TO '{tmp.as_posix()}' (FORMAT PARQUET, COMPRESSION ZSTD)"
            )
        finally:
            con.close()
        tmp.replace(path)
    return normalise(pl.scan_parquet(path))


def load_observed_bundle(include: tuple[str, ...] | None = None) -> dict[str, Any]:
    """Read-only load. New cache keys are prefixed IMP23. Existing keys are not rewritten.

    Called only outside synthetic mode. The pure functions above do not call it.
    """
    import _lab_base as B

    wanted = include or (
        "panel",
        "firms",
        "instalments",
        "protests",
        "payments",
        "cohort",
    )
    bundle: dict[str, Any] = {}
    if "panel" in wanted or "cohort" in wanted:
        bundle["panel"] = _normalise_panel(B.panel, B.attributes)
    if "firms" in wanted:
        bundle["firms"] = B.cached(
            "IMP23:firms",
            _read_firms,
            params={
                "ref": REF_RFB,
                "commit": MANIFEST_COMMIT,
                "cols": list(FIRM_COLUMNS),
            },
            version="1",
        ).collect()
    if "instalments" in wanted:
        bundle["instalments"] = _scan_sql(
            SQL_INSTALMENTS, _normalise_instalments, "IMP23-instalments"
        )
    if "protests" in wanted:
        bundle["protests"] = _scan_sql(
            SQL_PROTEST, _normalise_protests, "IMP23-protests"
        )
    if "payments" in wanted:
        bundle["payments"] = (
            B.scan_gare()
            .filter(pl.col("DATA_ARRECADACAO_GARE") <= pl.date(LAST_YEAR, 12, 31))
            .select(
                pl.col("ID_DEBITO").alias("id_debito"),
                pl.col("DATA_ARRECADACAO_GARE").alias("pay_date"),
                pl.col("VALOR_RECEITA").alias("amount"),
            )
        )
    if "cohort" in wanted:

        def _build_cohort() -> pl.DataFrame:
            return _cohort_cdas(bundle["panel"])

        bundle["cohort"] = B.cached(
            "IMP23:cohort",
            _build_cohort,
            params={"first": FIRST_YEAR, "last": LAST_YEAR},
            version="1",
        ).collect()
    return bundle


def _normalise_panel(panel: pl.LazyFrame, attributes: pl.LazyFrame) -> pl.LazyFrame:
    attr = (
        attributes.select(
            pl.col("ID_DEBITO").alias("id_debito"),
            pl.col("CNPJ").alias("cnpj"),
            pl.col("max_value").alias("enrolled_value"),
        )
        .group_by("id_debito")
        .agg(
            pl.col("cnpj").drop_nulls().first(),
            pl.col("enrolled_value").max(),
        )
    )
    base = panel.select(
        pl.col("ID_DEBITO").alias("id_debito"),
        pl.col("year"),
        pl.col("CNPJ_ROOT").alias("cnpj_root"),
        pl.col("ICMS_ORIGIN").alias("origin"),
        pl.col("DATA_INSCRICAO").alias("enrol_date"),
        pl.col("enrol_year"),
        pl.col("SITUACAO_DEBITO").alias("situacao"),
        pl.col("stock"),
        pl.col("paid"),
        pl.col("paid_flag"),
        pl.col("rate"),
        pl.col("rate_l1"),
        pl.col("age"),
        pl.col("present_in_december"),
    )
    return base.join(attr, on="id_debito", how="left")


def _normalise_instalments(df: pl.DataFrame) -> pl.DataFrame:
    return df.select(
        pl.col("ID_SOLICITACAOPARCELAMENTO").alias("id_solicitacao"),
        pl.col("ID_DEBITO").alias("id_debito"),
        pl.col("DATA_SOLICITACAO").alias("data_solicitacao"),
        pl.col("STATUS_PARCELAMENTO").alias("status_parcelamento"),
        pl.col("DATA_ROMPIMENTO").alias("data_rompimento"),
        pl.col("QTD_PARCELAS").alias("qtd_parcelas"),
    )


def _normalise_protests(df: pl.DataFrame) -> pl.DataFrame:
    return df.select(
        pl.col("ID_DEBITO").alias("id_debito"),
        pl.col("STATUS_PROTESTO").alias("status_protesto"),
        pl.col("DATA_PROTESTO").alias("data_protesto"),
        pl.col("VALOR_PROTESTADO").alias("valor_protestado"),
    )


def _read_firms() -> pl.DataFrame:
    """SHA-256 of the manifest, same commit as section 3.14, plus the size column."""
    import hashlib
    import json
    import subprocess
    from pathlib import Path

    import _lab_base as B
    from dotenv import dotenv_values

    out = subprocess.run(
        [
            "git",
            "-C",
            str(B.REPO),
            "show",
            f"{MANIFEST_COMMIT}:data_manifests/cnpj_enrich/cnpj_enriquecido_{REF_RFB}.json",
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    man = json.loads(out)
    edir = (
        Path(dotenv_values(B.REPO / ".env")["CNPJ_PIPELINE_WORKDIR"])
        / "build"
        / REF_RFB
        / "cnpj_enriquecido"
    )
    for item in man["arquivos"]:
        digest = hashlib.sha256((edir / item["arquivo"]).read_bytes()).hexdigest()
        if digest != item["sha256"]:
            raise RuntimeError("cnpj_enriquecido hash does not match the manifest")
    import duckdb

    con = duckdb.connect(
        config={
            "memory_limit": "2500MB",
            "threads": 2,
            "temp_directory": str(B.TMP),
            "preserve_insertion_order": False,
        }
    )
    try:
        cols = ", ".join(FIRM_COLUMNS)
        frame = con.execute(f"SELECT {cols} FROM read_parquet('{edir}/*.parquet')").pl()
    finally:
        con.close()
    n_distinct = frame["cnpj"].n_unique()
    if frame.height != n_distinct or frame.height != man["linhas"]:
        raise RuntimeError("cnpj_enriquecido row count does not match the manifest")
    return frame
