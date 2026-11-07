"""Build ICMS collection series for the closed v1 benchmark.

The target matches the v1 daily aggregate: sum of ``VALOR_TOTAL_GARE`` and
count of slips. No price index is applied. See the notebook for why a second
deflation step is not used.

Universe: legal-entity ICMS debts in the lab extract (CPF is about 0.5% of
December 2025 ICMS stock). Payments and stock snapshots after 2025-12-31 are
excluded. Weeks that would be labelled in 2026 are dropped.

Read-only on the external drive. DuckDB memory is capped, one year at a time.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pandas as pd

ICMS_ROOT = Path("/Volumes/Meedi_Etore_HD1/CEMEPI/interim/macro_eda/icms")
GARE_ROOT = Path("/Volumes/Meedi_Etore_HD1/CEMEPI/interim/macro_eda/gare_icms")
TEMP_DIR = Path("/Volumes/Meedi_Etore_HD1/CEMEPI/cache/tmp/ben_v1")
YEARS = range(2016, 2026)
CUT = "2025-12-31"
MEMORY = "2GB"

DECLARADO = "ICMS Declarado"
AUTUADO = "ICMS Autuação"


def article_dir() -> Path:
    here = Path(__file__).resolve()
    return (
        here.parents[1]
        / "notebooks"
        / "articles"
        / "arrecadacao_forecast_v1_segmentado"
    )


def duck(sql: str) -> None:
    TEMP_DIR.mkdir(parents=True, exist_ok=True)
    wrapped = f"""
    SET memory_limit='{MEMORY}';
    SET threads=2;
    SET preserve_insertion_order=false;
    SET temp_directory='{TEMP_DIR}';
    {sql}
    """
    subprocess.run(["duckdb", "-c", wrapped], check=True)


def build_maps(parts: Path, tmp: Path) -> Path:
    for year in YEARS:
        src = ICMS_ROOT / f"year={year}" / "part-0.parquet"
        dest = parts / f"id_tipo_{year}.parquet"
        duck(
            f"""
            COPY (
              SELECT ID_DEBITO,
                     arg_max(TIPO_DEBITO, MES_EXTRACAO) AS tipo,
                     {year} AS ano,
                     max(MES_EXTRACAO) AS mes
              FROM read_parquet('{src}')
              WHERE ANO_EXTRACAO = {year}
                AND MES_EXTRACAO BETWEEN 1 AND 12
              GROUP BY ID_DEBITO
            ) TO '{dest}' (FORMAT PARQUET);
            """
        )
        print(f"map {year}", flush=True)
    combined = tmp / "id_tipo.parquet"
    duck(
        f"""
        COPY (
          SELECT ID_DEBITO, arg_max(tipo, ano * 100 + mes) AS tipo
          FROM read_parquet('{parts / "id_tipo_*.parquet"}')
          GROUP BY ID_DEBITO
        ) TO '{combined}' (FORMAT PARQUET);
        """
    )
    return combined


def build_stock(parts: Path, tmp: Path) -> Path:
    for year in YEARS:
        src = ICMS_ROOT / f"year={year}" / "part-0.parquet"
        dest = parts / f"stock_{year}.parquet"
        duck(
            f"""
            COPY (
              SELECT ANO_EXTRACAO AS ano,
                     MES_EXTRACAO AS mes,
                     TIPO_DEBITO AS tipo,
                     SUM(TRY_CAST(VALOR_SEM_HONORARIOS AS DOUBLE)) AS stock,
                     COUNT(*) AS n_rows
              FROM read_parquet('{src}')
              WHERE ANO_EXTRACAO = {year}
                AND MES_EXTRACAO BETWEEN 1 AND 12
              GROUP BY 1, 2, 3
            ) TO '{dest}' (FORMAT PARQUET);
            """
        )
        print(f"stock {year}", flush=True)
    out = tmp / "stock_month_type.parquet"
    duck(
        f"""
        COPY (
          SELECT * FROM read_parquet('{parts / "stock_*.parquet"}')
        ) TO '{out}' (FORMAT PARQUET);
        """
    )
    return out


def build_daily(parts: Path, tmp: Path, id_tipo: Path) -> Path:
    for year in YEARS:
        src = GARE_ROOT / f"year={year}" / "part-0.parquet"
        dest = parts / f"gare_{year}.parquet"
        duck(
            f"""
            COPY (
              SELECT CAST(g.DATA_ARRECADACAO_GARE AS DATE) AS data,
                     COALESCE(t.tipo, 'UNMATCHED') AS tipo,
                     COUNT(*) AS n_guias,
                     SUM(g.VALOR_TOTAL_GARE) AS valor_sum,
                     SUM(g.VALOR_RECEITA) AS valor_receita,
                     SUM(g.JUROS_MORA) AS juros_mora,
                     SUM(g.MULTA_MORA) AS multa_mora
              FROM read_parquet('{src}') g
              LEFT JOIN read_parquet('{id_tipo}') t
                ON g.ID_DEBITO = t.ID_DEBITO
              WHERE g.DATA_ARRECADACAO_GARE <= DATE '{CUT}'
              GROUP BY 1, 2
            ) TO '{dest}' (FORMAT PARQUET);
            """
        )
        print(f"gare {year}", flush=True)
    out = tmp / "daily_type.parquet"
    duck(
        f"""
        COPY (
          SELECT data, tipo,
                 SUM(n_guias) AS n_guias,
                 SUM(valor_sum) AS valor_sum,
                 SUM(valor_receita) AS valor_receita,
                 SUM(juros_mora) AS juros_mora,
                 SUM(multa_mora) AS multa_mora
          FROM read_parquet('{parts / "gare_*.parquet"}')
          GROUP BY 1, 2
        ) TO '{out}' (FORMAT PARQUET);
        """
    )
    return out


def _segment_frame(daily: pd.DataFrame, tipos: list[str] | None) -> pd.DataFrame:
    part = daily if tipos is None else daily[daily["tipo"].isin(tipos)]
    grouped = (
        part.groupby("data", as_index=False)[["n_guias", "valor_sum"]]
        .sum()
        .sort_values("data")
    )
    start = grouped["data"].min()
    end = pd.Timestamp(CUT)
    idx = pd.date_range(start, end, freq="D")
    out = grouped.set_index("data").reindex(idx)
    out.index.name = "data"
    out["n_guias"] = out["n_guias"].fillna(0).astype("int64")
    out["valor_sum"] = out["valor_sum"].fillna(0.0)
    return out.reset_index()


def _monthly_panel(
    daily: pd.DataFrame, stock: pd.DataFrame, tipos: list[str] | None
) -> pd.DataFrame:
    month = daily.copy()
    month["month"] = month["data"].dt.to_period("M").dt.to_timestamp()
    panel = (
        month.groupby("month", as_index=False)[["valor_sum", "n_guias"]]
        .sum()
        .rename(columns={"valor_sum": "valor_total", "month": "data"})
    )
    if tipos is None:
        stk = stock.groupby(["ano", "mes"], as_index=False)["stock"].sum()
    else:
        stk = (
            stock[stock["tipo"].isin(tipos)]
            .groupby(["ano", "mes"], as_index=False)["stock"]
            .sum()
        )
    stk["data"] = pd.to_datetime({"year": stk["ano"], "month": stk["mes"], "day": 1})
    panel = panel.merge(stk[["data", "stock"]], on="data", how="left")
    panel = panel.rename(columns={"stock": "valor_sem_honorarios"})
    return panel.sort_values("data").reset_index(drop=True)


def _weekly(daily: pd.DataFrame) -> pd.DataFrame:
    week = (
        daily.set_index("data")
        .resample("W-SUN")
        .agg({"valor_sum": "sum", "n_guias": "sum"})
        .reset_index()
    )
    week = week[week["data"] <= CUT].copy()
    return week.sort_values("data").reset_index(drop=True)


def _business(daily: pd.DataFrame) -> pd.DataFrame:
    out = daily[daily["data"].dt.dayofweek < 5].copy()
    return out.sort_values("data").reset_index(drop=True)


def write_series(data_dir: Path, daily_path: Path, stock_path: Path) -> dict:
    daily_type = pd.read_parquet(daily_path)
    daily_type["data"] = pd.to_datetime(daily_type["data"])
    stock = pd.read_parquet(stock_path)
    segments = {
        "total": None,
        "declarado": [DECLARADO],
        "autuado": [AUTUADO],
    }
    summary = {}
    for name, tipos in segments.items():
        daily = _segment_frame(daily_type, tipos)
        if daily["data"].max() > pd.Timestamp(CUT):
            raise RuntimeError(f"{name} daily series passes {CUT}")
        if (daily["data"] > CUT).any():
            raise RuntimeError(f"{name} has a 2026 date")
        monthly = _monthly_panel(daily, stock, tipos)
        weekly = _weekly(daily)
        business = _business(daily)
        daily.to_csv(data_dir / f"{name}_daily.csv", index=False)
        business.to_csv(data_dir / f"{name}_business_daily.csv", index=False)
        weekly.to_csv(data_dir / f"{name}_weekly.csv", index=False)
        monthly.to_csv(data_dir / f"{name}_monthly.csv", index=False)
        summary[name] = {
            "daily_n": len(daily),
            "daily_start": str(daily["data"].min().date()),
            "daily_end": str(daily["data"].max().date()),
            "daily_zeros": int((daily["valor_sum"] == 0).sum()),
            "business_n": len(business),
            "weekly_n": len(weekly),
            "weekly_end": str(weekly["data"].max().date()),
            "monthly_n": len(monthly),
            "monthly_start": str(monthly["data"].min().date()),
            "monthly_end": str(monthly["data"].max().date()),
            "collection_sum": float(daily["valor_sum"].sum()),
        }
        print(name, summary[name], flush=True)
    return summary


def write_shares(data_dir: Path, daily_path: Path, stock_path: Path) -> None:
    daily = pd.read_parquet(daily_path)
    stock = pd.read_parquet(stock_path)
    snap = stock[(stock["ano"] == 2025) & (stock["mes"] == 12)].copy()
    snap["share"] = snap["stock"] / snap["stock"].sum()
    snap = snap.sort_values("stock", ascending=False)
    snap.to_csv(data_dir / "share_stock_2025_12.csv", index=False)
    coll = daily.groupby("tipo", as_index=False)[
        ["valor_sum", "valor_receita", "juros_mora", "multa_mora", "n_guias"]
    ].sum()
    coll["share_valor_total_gare"] = coll["valor_sum"] / coll["valor_sum"].sum()
    coll["parts"] = coll["valor_receita"] + coll["juros_mora"] + coll["multa_mora"]
    coll["parts_over_total"] = coll["parts"] / coll["valor_sum"]
    coll = coll.sort_values("valor_sum", ascending=False)
    coll.to_csv(data_dir / "share_collection_through_2025.csv", index=False)
    print("stock shares", flush=True)
    print(snap.to_string(index=False), flush=True)
    print("collection shares", flush=True)
    print(coll.to_string(index=False), flush=True)


def main() -> None:
    out = article_dir()
    data_dir = out / "data"
    tmp = data_dir / "_tmp"
    parts = tmp / "parts"
    data_dir.mkdir(parents=True, exist_ok=True)
    parts.mkdir(parents=True, exist_ok=True)
    print("stock scan", flush=True)
    stock_path = build_stock(parts, tmp)
    print("id map", flush=True)
    id_tipo = build_maps(parts, tmp)
    print("gare aggregate", flush=True)
    daily_path = build_daily(parts, tmp, id_tipo)
    summary = write_series(data_dir, daily_path, stock_path)
    write_shares(data_dir, daily_path, stock_path)
    (data_dir / "series_summary.json").write_text(json.dumps(summary, indent=2))
    print("wrote", data_dir, flush=True)


if __name__ == "__main__":
    main()
