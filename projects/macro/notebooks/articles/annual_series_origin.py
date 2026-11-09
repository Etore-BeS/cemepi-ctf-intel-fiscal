"""Annual stock and collection series for declared and assessed VAT debt.

Each year uses the last complete monthly snapshot. Rows are reduced to one
per debt identifier inside the snapshot before the totals are formed.
The database is attached read-only. Debt identifiers are not written.
"""

from __future__ import annotations

import argparse
import os
import tempfile
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

YEAR_MIN = 2016
YEAR_MAX = 2025
PAY_END = "2026-01-01"
PARTIAL_RATIO = 0.8
TYPES = (
    "ICMS Declarado",
    "ICMS Autuação",
    "ICMS Autuação Simples Nacional",
    "ICMS Fecoep",
    "ICMS Alteração de GIA",
)
COLOURS = {"assessed": "#154C56", "declared": "#BD9869"}


def find_repo(start: Path | None = None) -> Path:
    origin = start or Path.cwd()
    for path in [origin, *origin.parents]:
        if (path / "AGENTS.md").exists() and (path / "pyproject.toml").exists():
            return path
    raise RuntimeError("repository root not found")


def database_path() -> Path:
    if os.environ.get("CEMEPI_DUCKDB"):
        return Path(os.environ["CEMEPI_DUCKDB"])
    if os.environ.get("CEMEPI_DATA_ROOT"):
        return Path(os.environ["CEMEPI_DATA_ROOT"]) / "api_ctf" / "cemepi.duckdb"
    raise RuntimeError("Set CEMEPI_DUCKDB or CEMEPI_DATA_ROOT.")


def output_dirs(repo: Path | None = None) -> tuple[Path, Path]:
    root = repo or find_repo()
    base = root / "projects" / "macro" / "manuscript" / "icms_declarado_autuado"
    figures = base / "figures"
    tables = base / "tables"
    figures.mkdir(parents=True, exist_ok=True)
    tables.mkdir(parents=True, exist_ok=True)
    return figures, tables


def _types_sql() -> str:
    return ", ".join(f"'{name}'" for name in TYPES)


def _legal_entity_sql() -> str:
    """Filter to legal-entity debtors. The identifier is not selected."""
    cleaned = (
        "regexp_replace(upper(CAST(CNPJ_DEVEDOR AS VARCHAR)), '[^0-9A-Z]', '', 'g')"
    )
    normalised = (
        f"CASE WHEN regexp_full_match({cleaned}, '[0-9]{{12,13}}') "
        f"THEN lpad({cleaned}, 14, '0') ELSE nullif({cleaned}, '') END"
    )
    return f"length({normalised}) = 14"


def snapshot_status(frame: pd.DataFrame) -> pd.DataFrame:
    """Mark a month complete when it is present and not below the 80 percent screen."""
    out = frame.sort_values(["year", "month"]).reset_index(drop=True)
    out["ref_median7"] = out["cdas"].rolling(7, center=True, min_periods=3).median()
    missing = out["cdas"].isna() | (out["cdas"] <= 0)
    partial = (~missing) & (out["cdas"] < PARTIAL_RATIO * out["ref_median7"])
    out["complete"] = ~missing & ~partial
    return out


def last_complete_months(frame: pd.DataFrame) -> dict[int, int]:
    """Last complete month in each year. December 2024 fails the screen, so 2024 moves earlier."""
    status = frame if "complete" in frame.columns else snapshot_status(frame)
    chosen: dict[int, int] = {}
    for year, block in status.groupby("year"):
        ok = block.loc[block["complete"]]
        if ok.empty:
            raise RuntimeError(f"No complete snapshot in {int(year)}.")
        chosen[int(year)] = int(ok["month"].max())
    return chosen


def _connect():
    import duckdb

    database = database_path()
    if not database.exists():
        raise RuntimeError(f"Database file not found: {database.name}")
    scratch = Path(tempfile.mkdtemp(prefix="annual_series_"))
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


def _month_counts(connection, year: int) -> pd.DataFrame:
    """Counts after one row per debt identifier inside the month."""
    sql = f"""
        WITH raw AS (
            SELECT MES_EXTRACAO AS month, ID_DEBITO
            FROM src.DEBITO
            WHERE ANO_EXTRACAO = {year}
              AND MES_EXTRACAO BETWEEN 1 AND 12
              AND TIPO_DEBITO IN ({_types_sql()})
              AND {_legal_entity_sql()}
        ),
        one AS (
            SELECT month, ID_DEBITO
            FROM raw
            GROUP BY month, ID_DEBITO
        )
        SELECT {year} AS year, month, count(*) AS cdas
        FROM one
        GROUP BY month
    """
    return connection.execute(sql).df()


def _year_totals(connection, year: int, month: int) -> pd.DataFrame:
    """Stock from one deduplicated snapshot, and payments in that calendar year."""
    sql = f"""
        WITH raw AS (
            SELECT
                ID_DEBITO,
                VALOR_SEM_HONORARIOS AS value,
                CASE
                    WHEN TIPO_DEBITO = 'ICMS Declarado' THEN 'declared'
                    WHEN TIPO_DEBITO LIKE 'ICMS Autua%' THEN 'assessed'
                    ELSE 'other'
                END AS origin
            FROM src.DEBITO
            WHERE ANO_EXTRACAO = {year}
              AND MES_EXTRACAO = {month}
              AND TIPO_DEBITO IN ({_types_sql()})
              AND {_legal_entity_sql()}
              AND VALOR_SEM_HONORARIOS > 0
        ),
        one AS (
            SELECT ID_DEBITO, max(value) AS value, arg_max(origin, value) AS origin
            FROM raw
            GROUP BY ID_DEBITO
        ),
        pay AS (
            SELECT ID_DEBITO, sum(VALOR_RECEITA) AS paid
            FROM src.ARRECADACAO
            WHERE DATA_ARRECADACAO_GARE >= TIMESTAMP '{year}-01-01'
              AND DATA_ARRECADACAO_GARE < TIMESTAMP '{year + 1}-01-01'
              AND DATA_ARRECADACAO_GARE < TIMESTAMP '{PAY_END}'
            GROUP BY ID_DEBITO
        )
        SELECT
            {year} AS year,
            one.origin,
            count(*) AS cdas,
            sum(one.value) / 1e9 AS stock_bn,
            sum(coalesce(pay.paid, 0)) / 1e9 AS paid_bn
        FROM one
        LEFT JOIN pay USING (ID_DEBITO)
        GROUP BY one.origin
    """
    return connection.execute(sql).df()


def assemble_series(connection) -> tuple[pd.DataFrame, dict[int, int]]:
    parts = [_month_counts(connection, year) for year in range(YEAR_MIN, YEAR_MAX + 1)]
    counts = pd.concat(parts, ignore_index=True)
    grid = pd.DataFrame(
        [
            (year, month)
            for year in range(YEAR_MIN, YEAR_MAX + 1)
            for month in range(1, 13)
        ],
        columns=["year", "month"],
    )
    counts = grid.merge(counts, on=["year", "month"], how="left")
    status = snapshot_status(counts)
    chosen = last_complete_months(status)
    totals = pd.concat(
        [
            _year_totals(connection, year, month)
            for year, month in sorted(chosen.items())
        ],
        ignore_index=True,
    )
    totals["rate_pct"] = 100.0 * totals["paid_bn"] / totals["stock_bn"]
    declared = totals.loc[totals["origin"] == "declared", ["year", "paid_bn"]]
    pooled = (
        totals.loc[totals["origin"] != "other"]
        .groupby("year", as_index=False)["paid_bn"]
        .sum()
        .rename(columns={"paid_bn": "paid_two"})
    )
    share = declared.merge(pooled, on="year", how="left")
    share["pct_declared_in_paid"] = 100.0 * share["paid_bn"] / share["paid_two"]
    series = totals.merge(
        share[["year", "pct_declared_in_paid"]], on="year", how="left"
    )
    series = series.sort_values(["origin", "year"]).reset_index(drop=True)
    return series, chosen


def _figure(series: pd.DataFrame) -> go.Figure:
    shown = series.loc[series["origin"] != "other"]
    figure = make_subplots(
        rows=1,
        cols=2,
        subplot_titles=(
            "(a) Stock in the last complete snapshot (BRL bn)",
            "(b) Paid in year / stock (%, log)",
        ),
        horizontal_spacing=0.12,
    )
    for origin in ("assessed", "declared"):
        block = shown.loc[shown["origin"] == origin]
        figure.add_scatter(
            x=block["year"],
            y=block["stock_bn"],
            mode="lines+markers",
            name=origin,
            line={"color": COLOURS[origin]},
            row=1,
            col=1,
        )
        figure.add_scatter(
            x=block["year"],
            y=block["rate_pct"],
            mode="lines+markers",
            name=origin,
            line={"color": COLOURS[origin]},
            showlegend=False,
            row=1,
            col=2,
        )
    figure.update_yaxes(type="log", row=1, col=2)
    figure.update_xaxes(dtick=1)
    figure.update_layout(
        showlegend=True,
        template="simple_white",
        font={"family": "Arial, sans-serif", "size": 12, "color": "#154C56"},
        paper_bgcolor="white",
        plot_bgcolor="white",
    )
    return figure


def write_outputs(series: pd.DataFrame, repo: Path | None = None) -> None:
    figures, tables = output_dirs(repo)
    shown = series.loc[series["origin"] != "other"].copy()
    shown.to_csv(figures / "fig_annual_series_origin.csv", index=False)
    figure = _figure(series)
    figure.write_image(
        figures / "fig_annual_series_origin.png", width=700, height=340, scale=4
    )
    figure.write_image(figures / "fig_annual_series_origin.svg", width=700, height=340)
    series.to_csv(tables / "tab_annual_series_origin.csv", index=False)
    _write_table(series, tables / "tab_annual_series_origin.tex")


def _write_table(series: pd.DataFrame, path: Path) -> None:
    show = series.copy()
    for column, digits in (
        ("cdas", 0),
        ("stock_bn", 1),
        ("paid_bn", 3),
        ("rate_pct", 3),
        ("pct_declared_in_paid", 1),
    ):
        show[column] = show[column].map(
            lambda value, digits=digits: (
                "" if pd.isna(value) else f"{value:,.{digits}f}"
            )
        )
    header = " & ".join(str(column) for column in show.columns)
    body = "\n".join(
        " & ".join(str(value) for value in row) + r" \\"
        for row in show.itertuples(index=False)
    )
    path.write_text(
        "% generated by the supplementary material; do not edit by hand\n"
        "% One row per debt identifier in the last complete monthly snapshot of the year.\n"
        "\\begin{tabular}{lrrrrrr}\n\\toprule\n"
        f"{header} \\\\\n\\midrule\n{body}\n\\bottomrule\n\\end{{tabular}}\n",
        encoding="utf-8",
    )


def main(argv: list[str] | None = None) -> pd.DataFrame:
    parser = argparse.ArgumentParser(
        description="Rebuild the 2016-2025 stock and collection figure from read-only snapshots."
    )
    parser.parse_args(argv)
    connection = _connect()
    try:
        series, chosen = assemble_series(connection)
    finally:
        connection.close()
    write_outputs(series)
    months = ", ".join(f"{year}-{month:02d}" for year, month in sorted(chosen.items()))
    print(f"Snapshots used: {months}")
    return series


if __name__ == "__main__":
    main()
