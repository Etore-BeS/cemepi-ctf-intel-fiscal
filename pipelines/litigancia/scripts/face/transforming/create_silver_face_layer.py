#!/usr/bin/env python3
"""Bronze face Delta → silver face clean Delta (incremental, DuckDB anti-join)."""

from __future__ import annotations

import argparse
import gc
import json
import shutil
import sys
import uuid
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import pyarrow.compute as pc
import pyarrow.parquet as pq
from bcb import sgs
from deltalake import DeltaTable, write_deltalake
from tqdm import tqdm

from config.paths import BRONZE_FACE, LAKE_ROOT, SILVER_FACE_CLEAN

BRONZE_FACE_TABLE = BRONZE_FACE
SILVER_FACE_TABLE = SILVER_FACE_CLEAN
TMP_STAGE_DIR = LAKE_ROOT / "silver_layer" / ".tmp_face_staging"
ANOMALY_LOG_FILE = LAKE_ROOT / "silver_layer" / "anomalous_currencies.csv"
STATS_PATH = LAKE_ROOT / "silver_layer" / ".face_silver_stats.json"

# Lower than 50k to keep each pandas batch peak memory low on 8 GB machines.
BATCH_SIZE = 1000
SALARIO_MINIMO_2026 = 1623.0


def _escape_sql_literal(value: str) -> str:
    return value.replace("'", "''")


def _silver_readable() -> bool:
    return SILVER_FACE_TABLE.exists() and DeltaTable.is_deltatable(
        str(SILVER_FACE_TABLE)
    )


def _parquet_footer_row_count(table_path: Path) -> int:
    if not table_path.exists() or not DeltaTable.is_deltatable(str(table_path)):
        return 0
    dt = DeltaTable(str(table_path))
    total = 0
    for uri in dt.file_uris():
        total += pq.ParquetFile(uri).metadata.num_rows or 0
    return total


def _load_stats() -> dict | None:
    if not STATS_PATH.exists():
        return None
    try:
        return json.loads(STATS_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def _save_stats(stats: dict) -> None:
    STATS_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATS_PATH.write_text(json.dumps(stats, indent=2), encoding="utf-8")


def _pending_row_delta(bronze_rows: int, silver_rows: int) -> int:
    return max(0, bronze_rows - silver_rows)


def _refresh_stats_cache() -> dict:
    print(
        "Refreshing face silver stats (bronze row count is slow on many parquet files)..."
    )
    bronze_rows = _parquet_footer_row_count(BRONZE_FACE_TABLE)
    silver_rows = (
        _parquet_footer_row_count(SILVER_FACE_TABLE) if _silver_readable() else 0
    )
    pending_row_delta = _pending_row_delta(bronze_rows, silver_rows)
    pending_unique = (
        _count_pending_unique_except() if _silver_readable() else pending_row_delta
    )
    stats = {
        "bronze_rows": bronze_rows,
        "silver_rows": silver_rows,
        "pending_row_delta": pending_row_delta,
        "pending_unique": pending_unique,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    _save_stats(stats)
    return stats


def _sync_stats_after_publish(*, published_unique: int = 0) -> None:
    stats = _load_stats() or {}
    silver_rows = (
        _parquet_footer_row_count(SILVER_FACE_TABLE) if _silver_readable() else 0
    )
    stats["silver_rows"] = silver_rows
    if "bronze_rows" in stats:
        stats["pending_row_delta"] = _pending_row_delta(
            int(stats["bronze_rows"]), silver_rows
        )
    if _silver_readable():
        if "pending_unique" in stats:
            stats["pending_unique"] = max(
                0, int(stats["pending_unique"]) - published_unique
            )
        else:
            stats["pending_unique"] = _count_pending_unique_except()
    stats["updated_at"] = datetime.now(timezone.utc).isoformat()
    _save_stats(stats)


def count_pending_stats(
    *,
    include_unique: bool = False,
    refresh: bool = False,
) -> tuple[int, int]:
    """Return pending work and optional row-delta diagnostic.

    Pending work includes both never-published cd_processo values and newer
    bronze re-scrapes whose ingested_at is fresher than silver for the same
    cd_processo.
    """
    if not BRONZE_FACE_TABLE.exists():
        return 0, 0

    if refresh or _load_stats() is None:
        stats = _refresh_stats_cache()
    else:
        stats = _load_stats()
        silver_rows = (
            _parquet_footer_row_count(SILVER_FACE_TABLE) if _silver_readable() else 0
        )
        stats["silver_rows"] = silver_rows
        if "bronze_rows" in stats:
            stats["pending_row_delta"] = _pending_row_delta(
                int(stats["bronze_rows"]), silver_rows
            )
        if _silver_readable() and "pending_unique" not in stats:
            print(
                "Computing unique pending (one-time slow DuckDB query)...",
                file=sys.stderr,
            )
            stats["pending_unique"] = _count_pending_unique_except()
        _save_stats(stats)

    pending_row_delta = int(stats.get("pending_row_delta", 0))
    if _silver_readable():
        pending_work = int(stats.get("pending_unique", pending_row_delta))
    else:
        pending_work = pending_row_delta

    if _silver_readable() and pending_row_delta > 0:
        pending_new_or_newer_rows, pending_new_or_newer_unique = (
            _count_pending_new_or_newer()
        )
        pending_work = pending_new_or_newer_rows
    else:
        pending_new_or_newer_unique = pending_work

    if include_unique:
        return pending_work, pending_new_or_newer_unique

    row_delta = pending_row_delta if _silver_readable() else -1
    return pending_work, row_delta


def _count_pending_unique_except() -> int:
    bronze_lit = _escape_sql_literal(str(BRONZE_FACE_TABLE))
    silver_lit = _escape_sql_literal(str(SILVER_FACE_TABLE))
    con = duckdb.connect()
    try:
        row = con.execute(
            f"""
            SELECT count(*) FROM (
                SELECT DISTINCT cd_processo
                FROM delta_scan('{bronze_lit}')
                WHERE cd_processo IS NOT NULL
                EXCEPT
                SELECT DISTINCT cd_processo
                FROM delta_scan('{silver_lit}')
                WHERE cd_processo IS NOT NULL
            ) AS pending
            """
        ).fetchone()
        return int(row[0]) if row else 0
    finally:
        con.close()


def _count_pending_new_or_newer() -> tuple[int, int]:
    bronze_lit = _escape_sql_literal(str(BRONZE_FACE_TABLE))
    silver_lit = _escape_sql_literal(str(SILVER_FACE_TABLE))
    con = duckdb.connect()
    try:
        row = con.execute(
            f"""
            WITH silver_latest AS (
                SELECT cd_processo, max(ingested_at) AS silver_ingested_at
                FROM delta_scan('{silver_lit}')
                WHERE cd_processo IS NOT NULL
                GROUP BY cd_processo
            ), pending AS (
                SELECT b.cd_processo
                FROM delta_scan('{bronze_lit}') AS b
                LEFT JOIN silver_latest AS s
                ON b.cd_processo = s.cd_processo
                WHERE b.cd_processo IS NOT NULL
                  AND (
                    s.cd_processo IS NULL
                    OR b.ingested_at > s.silver_ingested_at
                  )
            )
            SELECT count(*) AS pending_rows, count(DISTINCT cd_processo) AS pending_unique
            FROM pending
            """
        ).fetchone()
        if not row:
            return 0, 0
        return int(row[0]), int(row[1])
    finally:
        con.close()


def _pending_batches_sql(max_rows: int | None) -> str:
    bronze_lit = _escape_sql_literal(str(BRONZE_FACE_TABLE))
    if not _silver_readable():
        base = f"SELECT * FROM delta_scan('{bronze_lit}')"
    else:
        silver_lit = _escape_sql_literal(str(SILVER_FACE_TABLE))
        base = f"""
            SELECT b.*
            FROM delta_scan('{bronze_lit}') AS b
            LEFT JOIN (
                SELECT cd_processo, max(ingested_at) AS silver_ingested_at
                FROM delta_scan('{silver_lit}')
                WHERE cd_processo IS NOT NULL
                GROUP BY cd_processo
            ) AS s
            ON b.cd_processo = s.cd_processo
            WHERE b.cd_processo IS NOT NULL
              AND (
                s.cd_processo IS NULL
                OR b.ingested_at > s.silver_ingested_at
              )
        """
    if max_rows is not None:
        return f"SELECT * FROM ({base}) AS pending LIMIT {int(max_rows)}"
    return base


def iter_pending_batches(
    batch_size: int,
    max_rows: int | None,
) -> Iterator[pd.DataFrame]:
    con = duckdb.connect()
    try:
        result = con.execute(_pending_batches_sql(max_rows))
        while True:
            chunk = result.fetch_df_chunk(batch_size)
            if chunk is None or chunk.empty:
                break
            yield chunk
    finally:
        con.close()


# -------------------------------------------------------------------
# FUNÇÕES AUXILIARES E DO BANCO CENTRAL
# -------------------------------------------------------------------
def get_ipca_series() -> pd.DataFrame:
    """Baixa a série histórica do IPCA (Código 433) do BCB e prepara os fatores."""
    try:
        ipca_df = sgs.get({"IPCA": 433}, start="1995-01-01")

        if ipca_df is None or ipca_df.empty:
            raise ValueError("O Banco Central retornou uma tabela vazia.")

        ipca_df["fator_mensal"] = 1 + (ipca_df["IPCA"] / 100.0)
        ipca_df.index = pd.to_datetime(ipca_df.index)

        return ipca_df
    except Exception as e:
        raise ConnectionError(f"Falha na API do SGS/BCB: {e}") from e


def get_latest_date(json_date_str):
    if pd.isna(json_date_str) or not isinstance(json_date_str, str):
        return None
    try:
        dates_list = json.loads(json_date_str)
        if not dates_list or dates_list == [None]:
            return None
        valid_dates = []
        for d in dates_list:
            if d:
                try:
                    valid_dates.append(datetime.strptime(d.strip(), "%d/%m/%Y"))
                except ValueError:
                    continue
        if not valid_dates:
            return None
        return max(valid_dates).strftime("%d/%m/%Y")
    except json.JSONDecodeError:
        return None


# -------------------------------------------------------------------
# REGRAS DE NEGÓCIO E CORREÇÃO MONETÁRIA
# -------------------------------------------------------------------
def apply_business_rules(
    df: pd.DataFrame, ipca_df: pd.DataFrame, dicionario_fatores: dict
) -> pd.DataFrame:
    if "data_sentenca_clean" in df.columns and "distribuicao_data" in df.columns:
        dias_tramitacao = (df["data_sentenca_clean"] - df["distribuicao_data"]).dt.days
        df["tempo_tramitacao_meses"] = (dias_tramitacao / 30.44).round(2)

    if "valor_limpo" in df.columns and "distribuicao_data" in df.columns:
        df["ano_mes"] = df["distribuicao_data"].dt.strftime("%Y-%m")
        meses_unicos = df["ano_mes"].dropna().unique()

        for mes_ano in meses_unicos:
            if mes_ano not in dicionario_fatores:
                if ipca_df is not None:
                    start_date = pd.to_datetime(f"{mes_ano}-01")
                    mask = ipca_df.index >= start_date

                    if mask.any():
                        fator_acumulado = ipca_df.loc[mask, "fator_mensal"].prod()
                        dicionario_fatores[mes_ano] = float(fator_acumulado)
                    else:
                        dicionario_fatores[mes_ano] = 1.0
                else:
                    dicionario_fatores[mes_ano] = 1.0

        df["fator_correcao_ipca"] = df["ano_mes"].map(dicionario_fatores).fillna(1.0)
        df["valor_corrigido_atual"] = (
            df["valor_limpo"] * df["fator_correcao_ipca"]
        ).round(2)
        df["valor_sm"] = (df["valor_corrigido_atual"] / SALARIO_MINIMO_2026).round(2)

        colunas_lixo = ["ano_mes", "ipca_ref_04_2026"]
        df = df.drop(
            columns=[c for c in colunas_lixo if c in df.columns], errors="ignore"
        )

    df["updated_at"] = pd.Timestamp.now(tz="UTC")

    return df


# -------------------------------------------------------------------
# BACKFILL
# -------------------------------------------------------------------
def backfill_existing_silver_data(
    *,
    force_backfill: bool = False,
    skip_backfill: bool = False,
) -> None:
    if skip_backfill:
        return
    if not _silver_readable():
        return

    print("\nVerificando se há necessidade de Backfill Mensal (Atualização de IPCA)...")

    dt_silver = DeltaTable(str(SILVER_FACE_TABLE))
    schema = dt_silver.schema().to_arrow()

    if "updated_at" in schema.names and not force_backfill:
        pa_table = dt_silver.to_pyarrow_table(columns=["updated_at"])
        max_date = pc.max(pa_table["updated_at"]).as_py()
        mes_ano_atual = datetime.now().strftime("%m-%Y")

        if max_date and max_date.strftime("%m-%Y") == mes_ano_atual:
            print(
                f"-> A base já foi atualizada neste mês ({mes_ano_atual}). Backfill ignorado!"
            )
            return

    if force_backfill:
        print("MODO FORCE_BACKFILL: Ignorando a trava e forçando recálculo!")
    else:
        print("Mês virou ou primeira execução detectada! Rodando Backfill...")

    tmp_silver_backfill = LAKE_ROOT / "silver_layer" / ".tmp_silver_backfill"
    if tmp_silver_backfill.exists():
        shutil.rmtree(tmp_silver_backfill)
    tmp_silver_backfill.mkdir(parents=True, exist_ok=True)

    print("Baixando série histórica do IPCA via python-bcb...")
    try:
        ipca_df = get_ipca_series()
    except Exception as e:
        print(f"\nERRO CRÍTICO: {e}")
        print(
            "O Backfill foi CANCELADO para não sobrescrever a base com valores sem correção."
        )
        return

    fator_global_cache: dict = {}
    dataset = dt_silver.to_pyarrow_dataset()
    total_rows = dataset.count_rows()

    progress_bar = tqdm(total=total_rows, desc="Backfill Silver", unit="rows")

    for i, batch in enumerate(dataset.scanner(batch_size=BATCH_SIZE).to_batches()):
        df_batch = batch.to_pandas()
        df_batch = apply_business_rules(df_batch, ipca_df, fator_global_cache)

        if i == 0:
            write_deltalake(
                str(tmp_silver_backfill),
                df_batch,
                mode="overwrite",
                schema_mode="overwrite",
            )
        else:
            write_deltalake(
                str(tmp_silver_backfill), df_batch, mode="append", schema_mode="merge"
            )

        progress_bar.update(len(df_batch))
        del df_batch
        gc.collect()

    progress_bar.close()

    print("Substituindo a tabela Silver antiga pela versão atualizada...")
    shutil.rmtree(str(SILVER_FACE_TABLE))
    shutil.move(str(tmp_silver_backfill), str(SILVER_FACE_TABLE))
    print("Backfill concluído com sucesso!")


def _transform_batch(df: pd.DataFrame, anomalous_currencies: list) -> pd.DataFrame:
    df["num_processo_limpo"] = (
        df["numero"].fillna("").astype(str).str.replace(r"\D", "", regex=True)
    )
    df["num_processo_limpo"] = df["num_processo_limpo"].replace("", np.nan)

    extracted_dates = (
        df["distribuicao"].astype(str).str.extract(r"(\d{2}/\d{2}/\d{4})")[0]
    )
    df["distribuicao_data"] = pd.to_datetime(
        extracted_dates, format="%d/%m/%Y", errors="coerce"
    )

    valores_brutos = df["valor"].astype(str).str.strip()
    mask_anomaly = ~valores_brutos.str.startswith("R$") & ~valores_brutos.isin(
        ["NÃO HÁ REGISTRO", "nan", ""]
    )

    if mask_anomaly.any():
        anomalous_subset = df[mask_anomaly][["cd_processo", "valor"]].copy()
        anomalous_currencies.extend(anomalous_subset.to_dict("records"))

    def clean_currency(val_str):
        if val_str in ["NÃO HÁ REGISTRO", "nan", ""]:
            return np.nan
        cleaned = val_str.replace("R$", "").replace(".", "").replace(",", ".").strip()
        try:
            return float(cleaned)
        except ValueError:
            return np.nan

    df["valor_limpo"] = valores_brutos.apply(clean_currency)
    df["data_sentenca_clean"] = df["data_sentença"].apply(get_latest_date)
    df["data_sentenca_clean"] = pd.to_datetime(
        df["data_sentenca_clean"], format="%d/%m/%Y", errors="coerce"
    )
    return df


# -------------------------------------------------------------------
# INGESTÃO DA BRONZE -> SILVER
# -------------------------------------------------------------------
def process_bronze_face_to_silver(
    *,
    max_rows: int | None = None,
    force_backfill: bool = False,
    skip_backfill: bool = False,
    pending_rows: int | None = None,
    compact_after: bool = False,
) -> int:
    if not BRONZE_FACE_TABLE.exists():
        print(f"Bronze table not found at {BRONZE_FACE_TABLE}")
        return -1

    backfill_existing_silver_data(
        force_backfill=force_backfill,
        skip_backfill=skip_backfill,
    )

    if pending_rows is None:
        pending_rows, _ = count_pending_stats()
    if pending_rows == 0:
        print(
            "Todos os processos da Bronze já foram extraídos. Nada novo para processar!"
        )
        return 0

    wave_rows = pending_rows if max_rows is None else min(pending_rows, max_rows)
    if max_rows is not None:
        print(f"Capping this run to {wave_rows:,} row(s).")

    if TMP_STAGE_DIR.exists():
        shutil.rmtree(TMP_STAGE_DIR)
    TMP_STAGE_DIR.mkdir(parents=True, exist_ok=True)

    print(f"\nFase 1: Extraindo e Limpando ~{wave_rows:,} registros pendentes...")

    try:
        ipca_df = get_ipca_series()
    except Exception as e:
        print(f"\nERRO: {e}. Abortando extração de novos processos.")
        return -1

    fator_global_cache: dict = {}
    progress_bar = tqdm(total=wave_rows, desc="Staging Data", unit="rows")
    anomalous_currencies: list = []
    rows_staged = 0

    for df_raw in iter_pending_batches(BATCH_SIZE, max_rows):
        rows_to_process = len(df_raw)
        if rows_to_process == 0:
            continue

        df = _transform_batch(df_raw, anomalous_currencies)
        df = apply_business_rules(df, ipca_df, fator_global_cache)
        df = df.drop_duplicates(subset=["cd_processo"], keep="last")

        temp_file = TMP_STAGE_DIR / f"stage_{uuid.uuid4().hex}.parquet"
        df.to_parquet(temp_file, index=False)

        rows_staged += rows_to_process
        progress_bar.update(rows_to_process)

    progress_bar.close()

    if anomalous_currencies:
        df_anomalies = pd.DataFrame(anomalous_currencies)
        if ANOMALY_LOG_FILE.exists():
            df_anomalies.to_csv(ANOMALY_LOG_FILE, mode="a", header=False, index=False)
        else:
            df_anomalies.to_csv(ANOMALY_LOG_FILE, index=False)

    print("\nFase 2: Publicando novos dados na Silver (append)...")
    staged_files = sorted(
        TMP_STAGE_DIR.glob("*.parquet"), key=lambda f: f.stat().st_mtime
    )

    if not staged_files:
        shutil.rmtree(TMP_STAGE_DIR)
        print("Nenhum cd_processo novo encontrado na Bronze (re-scrapes já na Silver).")
        return rows_staged

    df_list = [pd.read_parquet(f) for f in staged_files]
    df_chunk = pd.concat(df_list, ignore_index=True)
    del df_list

    df_chunk = df_chunk.drop_duplicates(subset=["cd_processo"], keep="last")

    if df_chunk.empty:
        shutil.rmtree(TMP_STAGE_DIR)
        return rows_staged

    published_unique = int(df_chunk["cd_processo"].nunique())

    silver_exists = _silver_readable()
    if not silver_exists:
        write_deltalake(str(SILVER_FACE_TABLE), df_chunk, mode="overwrite")
    else:
        write_deltalake(
            str(SILVER_FACE_TABLE), df_chunk, mode="append", schema_mode="merge"
        )

    del df_chunk
    gc.collect()

    _sync_stats_after_publish(published_unique=published_unique)

    if compact_after and _silver_readable():
        print("Otimizando arquivos Delta da Silver...")
        DeltaTable(str(SILVER_FACE_TABLE)).optimize.compact()

    shutil.rmtree(TMP_STAGE_DIR)
    print(
        f"\nSilver Layer atualizada! {rows_staged:,} linha(s) processadas. "
        f"Destino: {SILVER_FACE_TABLE}"
    )
    return rows_staged


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Transform bronze face Delta into silver face clean Delta (incremental)."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print pending row count only (fast).",
    )
    parser.add_argument(
        "--refresh-counts",
        action="store_true",
        help="Recount bronze rows from parquet footers (slow; ~17k files).",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="With --dry-run, also count unique cd_processo (slow DuckDB EXCEPT).",
    )
    parser.add_argument(
        "--max-rows",
        type=int,
        default=None,
        metavar="N",
        help="Cap bronze rows processed in this run (for overnight batches).",
    )
    parser.add_argument(
        "--force-backfill",
        action="store_true",
        help="Force IPCA recalculation on existing silver rows.",
    )
    parser.add_argument(
        "--skip-backfill",
        action="store_true",
        help="Skip monthly IPCA backfill check (useful in overnight loops).",
    )
    parser.add_argument(
        "--compact",
        action="store_true",
        help="Run Delta compact() after publish (slow on USB; off by default).",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if not BRONZE_FACE_TABLE.exists():
        print(f"Bronze table not found at {BRONZE_FACE_TABLE}")
        return 1

    pending_work, extra = count_pending_stats(
        include_unique=args.verbose,
        refresh=args.refresh_counts,
    )
    print(f"Bronze face rows pending (new or fresher than silver): {pending_work:,}")
    if args.verbose:
        print(f"Bronze cd_processo pending unique (live): {extra:,}")
    elif extra >= 0:
        print(f"Bronze−silver row delta (re-scrapes): {extra:,}")
    print(f"pending_work={pending_work}")

    if args.dry_run:
        return 0
    if pending_work == 0:
        print("Nothing to process.")
        return 0

    processed = process_bronze_face_to_silver(
        max_rows=args.max_rows,
        force_backfill=args.force_backfill,
        skip_backfill=args.skip_backfill,
        pending_rows=pending_work,
        compact_after=args.compact,
    )
    if processed < 0:
        return 1
    return 0 if processed > 0 or pending_work == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
