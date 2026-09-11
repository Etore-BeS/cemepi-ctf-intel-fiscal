import argparse
import gc
import json
import math
import re
import sys
import uuid
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

import duckdb
import ijson
import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq
from deltalake import DeltaTable, write_deltalake
from deltalake.schema import Field, PrimitiveType
from tqdm import tqdm

from config.paths import BRONZE_COLETAS, COLLECT_ROOT, LAKE_ROOT, SILVER_PROCESSOS
from config.scripts import CREATE_SILVER_LAYER, format_command

BRONZE_TABLE_PATH = BRONZE_COLETAS
SILVER_TABLE_PATH = SILVER_PROCESSOS
TMP_STAGE_DIR = LAKE_ROOT / "silver_layer" / ".tmp_staging"
CHECKPOINT_PATH = LAKE_ROOT / "silver_layer" / ".silver_bronze_paths.json"

JSON_BRONZE_SUFFIX = "process_grouped_all_assuntos.json"
DB_SUFFIX = "process_grouped_all_assuntos.db"
SQLITE_ITEMS_TABLE = "processos_comunicacoes_consolidado"
SILVER_ITEM_COLUMNS = (
    "cd_processo",
    "id_processo",
    "classe",
    "assunto",
    "magistrado",
    "comarca",
    "foro",
    "vara",
    "data_disponibilizacao",
    "decisao",
)

# Flush expanded silver "items" to Parquet after this many rows (bounds RAM).
ITEM_FLUSH_THRESHOLD = 10_000
# Publish deduped Parquet to Delta in chunks of this size.
PUBLISH_BATCH_ROWS = 10_000
# Backward compat alias
MERGE_BATCH_ROWS = PUBLISH_BATCH_ROWS


def _escape_sql_literal(value: str) -> str:
    return value.replace("'", "''")


def _coerce_silver_field(value: object) -> str | None:
    """SQLite NULL → pandas/duckdb NaN must not reach PyArrow as float."""
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if pd.isna(value):
        return None
    if not isinstance(value, str):
        return str(value)
    return value


def clean_and_deduplicate_decisao(text: str | None) -> str | None:
    text = _coerce_silver_field(text)
    if text is None or not text.strip():
        return text

    pattern = r"(?:\s*\|\s*)?-+\s*\d+\s*-+\s*\|\s*"
    chunks = re.split(pattern, text)

    seen = set()
    unique_chunks = []

    for chunk in chunks:
        clean_chunk = chunk.strip()
        if clean_chunk and clean_chunk not in seen:
            seen.add(clean_chunk)
            unique_chunks.append(clean_chunk)

    return "\n\n--- [NOVA DECISÃO/MOVIMENTAÇÃO] ---\n\n".join(unique_chunks)


def _normalize_item_row(row: object, source_bronze_path: str) -> dict | None:
    if not isinstance(row, dict):
        return None
    out = {col: _coerce_silver_field(row.get(col)) for col in SILVER_ITEM_COLUMNS}
    out["source_bronze_path"] = source_bronze_path
    out["decisao"] = clean_and_deduplicate_decisao(out.get("decisao"))
    return out


def _dedupe_rows_keep_last(rows: list[dict]) -> list[dict]:
    last_by_key: dict[tuple, dict] = {}
    for row in rows:
        last_by_key[(row.get("id_processo"), row.get("decisao"))] = row
    return list(last_by_key.values())


def flush_items_chunk(
    chunk: list,
    seq_state: dict,
    tmp_dir: Path,
) -> None:
    """Write one buffered chunk of items to staging Parquet (PyArrow, ingest order for dedupe)."""
    if not chunk:
        return

    normalized = []
    for row in chunk:
        if isinstance(row, dict) and "source_bronze_path" in row:
            item = dict(row)
            if "decisao" in item:
                item["decisao"] = clean_and_deduplicate_decisao(item.get("decisao"))
            normalized.append(item)
        else:
            tqdm.write("WARNING: skipping row without source_bronze_path")

    deduped = _dedupe_rows_keep_last(normalized)
    if not deduped:
        return

    seq = seq_state["seq"]
    n = len(deduped)
    ingest_seq = list(range(seq, seq + n))
    seq_state["seq"] = seq + n

    table = pa.Table.from_pylist(deduped)
    table = table.append_column("_ingest_seq", pa.array(ingest_seq, type=pa.int64()))

    temp_file = tmp_dir / f"stage_{uuid.uuid4().hex}.parquet"
    pq.write_table(table, temp_file, compression="zstd")
    del table, deduped, normalized
    gc.collect()


def _stream_items_from_disk(json_path: Path, source_bronze_path: str):
    with open(json_path, "rb") as f:
        for row in ijson.items(f, "items.item"):
            item = _normalize_item_row(row, source_bronze_path)
            if item is not None:
                yield item


def _iter_items_from_raw_json(raw_json: str, source_bronze_path: str):
    data = json.loads(raw_json)
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return
    for row in items:
        item = _normalize_item_row(row, source_bronze_path)
        if item is not None:
            yield item


def _fetch_legacy_raw_data(paths: set[str]) -> dict[str, str]:
    """Bronze raw_data for paths missing on disk (legacy rows before metadata-only bronze)."""
    need = {p for p in paths if not Path(p).is_file()}
    if not need or not BRONZE_TABLE_PATH.exists():
        return {}
    if not DeltaTable.is_deltatable(str(BRONZE_TABLE_PATH)):
        return {}

    dt = DeltaTable(str(BRONZE_TABLE_PATH))
    dataset = dt.to_pyarrow_dataset()
    filter_expr = pc.field("file_path").isin(pa.array(sorted(need)))
    scanner = dataset.scanner(
        filter=filter_expr,
        columns=["file_path", "raw_data"],
        batch_size=100,
    )

    latest: dict[str, str] = {}
    for batch in scanner.to_batches():
        file_paths = batch.column("file_path").to_pylist()
        raw_values = batch.column("raw_data").to_pylist()
        for file_path, raw_data in zip(file_paths, raw_values):
            if isinstance(file_path, str) and raw_data is not None:
                latest[file_path] = raw_data
    return latest


def _buffer_items(
    item_iter,
    item_buffer: list,
    seq_state: dict,
    tmp_dir: Path,
) -> None:
    for item in item_iter:
        item_buffer.append(item)
        while len(item_buffer) >= ITEM_FLUSH_THRESHOLD:
            flush_items_chunk(
                item_buffer[:ITEM_FLUSH_THRESHOLD],
                seq_state,
                tmp_dir,
            )
            del item_buffer[:ITEM_FLUSH_THRESHOLD]


def _sibling_db_path(json_path: Path) -> Path:
    return json_path.with_name(DB_SUFFIX)


def _lake_duckdb_connect() -> duckdb.DuckDBPyConnection:
    """DuckDB tuned for large spill on the lake volume (staging dedupe + compact)."""
    temp_dir = TMP_STAGE_DIR / "duckdb_lake_temp"
    temp_dir.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    con.execute(f"SET temp_directory='{_sql_path_literal(temp_dir)}'")
    con.execute("SET preserve_insertion_order=false")
    con.execute("SET threads=2")
    con.execute("SET memory_limit='6GB'")
    return con


def _stage_from_sqlite(
    db_path: Path,
    source_bronze_path: str,
    run_stage_dir: Path,
) -> None:
    """Chunked read from per-month SQLite into staging parquet (bounded RAM)."""
    print(f"Staging source: sqlite ({db_path})", flush=True)
    _validate_collector_path(source_bronze_path)
    db_lit = _sql_path_literal(db_path.resolve())
    cols = ", ".join(SILVER_ITEM_COLUMNS)
    con = _lake_duckdb_connect()
    try:
        count_row = con.execute(
            f"SELECT COUNT(*) FROM sqlite_scan('{db_lit}', '{SQLITE_ITEMS_TABLE}')"
        ).fetchone()
        if count_row is None:
            return
        total = int(count_row[0])
        if total == 0:
            return

        seq_state = {"seq": 0}
        item_buffer: list = []
        for offset in tqdm(
            range(0, total, ITEM_FLUSH_THRESHOLD),
            desc="SQLite chunks",
            unit="chunk",
            leave=False,
        ):
            limit = min(ITEM_FLUSH_THRESHOLD, total - offset)
            chunk_df = con.execute(
                f"""
                SELECT {cols}
                FROM sqlite_scan('{db_lit}', '{SQLITE_ITEMS_TABLE}')
                LIMIT {limit} OFFSET {offset}
                """
            ).fetchdf()
            for record in chunk_df.to_dict(orient="records"):
                item = _normalize_item_row(record, source_bronze_path)
                if item is not None:
                    item_buffer.append(item)
            while len(item_buffer) >= ITEM_FLUSH_THRESHOLD:
                flush_items_chunk(
                    item_buffer[:ITEM_FLUSH_THRESHOLD],
                    seq_state,
                    run_stage_dir,
                )
                del item_buffer[:ITEM_FLUSH_THRESHOLD]
            del chunk_df
            gc.collect()

        if item_buffer:
            flush_items_chunk(item_buffer, seq_state, run_stage_dir)
            item_buffer.clear()
    finally:
        con.close()


def _stage_collector_paths(
    paths_to_process: set[str],
    run_stage_dir: Path,
) -> bool:
    """Stream collector data into staging parquet. Returns True if any items were staged."""
    legacy_raw = _fetch_legacy_raw_data(paths_to_process)
    seq_state = {"seq": 0}
    item_buffer: list = []
    staged_any = False

    for path_str in tqdm(sorted(paths_to_process), desc="Staging paths", unit="path"):
        disk_path = Path(path_str)
        if disk_path.is_file():
            _validate_collector_path(path_str)
            db_path = _sibling_db_path(disk_path)
            if db_path.is_file():
                _stage_from_sqlite(db_path, path_str, run_stage_dir)
                staged_any = True
            else:
                print(f"Staging source: json (ijson) ({disk_path})", flush=True)
                _buffer_items(
                    _stream_items_from_disk(disk_path, path_str),
                    item_buffer,
                    seq_state,
                    run_stage_dir,
                )
                staged_any = True
        elif path_str in legacy_raw:
            tqdm.write(f"Legacy bronze raw_data for {path_str}")
            _buffer_items(
                _iter_items_from_raw_json(legacy_raw[path_str], path_str),
                item_buffer,
                seq_state,
                run_stage_dir,
            )
            staged_any = True
        else:
            tqdm.write(f"WARNING: no JSON on disk and no bronze raw_data: {path_str}")

    if item_buffer:
        flush_items_chunk(item_buffer, seq_state, run_stage_dir)
        item_buffer.clear()
        staged_any = True

    return staged_any


def _validate_collector_path(path_str: str) -> None:
    if "'" in path_str:
        raise ValueError(f"Invalid file_path: {path_str}")
    resolved = Path(path_str).resolve()
    if not resolved.is_relative_to(COLLECT_ROOT.resolve()):
        raise ValueError(f"file_path must be under COLLECT_ROOT: {path_str}")


def _sql_path_literal(path: Path) -> str:
    return str(path).replace("'", "''")


def build_deduped_parquet(stage_dir: Path, dedupe_path: Path) -> int:
    """Global dedupe (id_processo, decisao) keeping latest ingest — spilled by DuckDB, not pandas."""
    staged_files = sorted(stage_dir.glob("stage_*.parquet"))
    if not staged_files:
        raise ValueError(f"No staging files to dedupe under {stage_dir}")

    missing = [p for p in staged_files if not p.is_file()]
    if missing:
        raise FileNotFoundError(
            f"{len(missing)} staging parquet(s) missing (another process may have "
            f"deleted {TMP_STAGE_DIR}). Re-run after ensuring only one silver job is active. "
            f"Example missing: {missing[0]}"
        )

    stage_glob = _sql_path_literal(stage_dir / "stage_*.parquet")
    dedupe_sql = _sql_path_literal(dedupe_path)

    con = _lake_duckdb_connect()
    try:
        con.execute(
            f"""
            COPY (
                SELECT * EXCLUDE (rn, _ingest_seq) FROM (
                    SELECT
                        *,
                        ROW_NUMBER() OVER (
                            PARTITION BY id_processo, decisao
                            ORDER BY _ingest_seq DESC
                        ) AS rn
                    FROM read_parquet('{stage_glob}')
                )
                WHERE rn = 1
            )
            TO '{dedupe_sql}'
            (FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE {PUBLISH_BATCH_ROWS})
            """
        )
        row = con.execute(
            f"SELECT COUNT(*) FROM read_parquet('{dedupe_sql}')"
        ).fetchone()
        return int(row[0]) if row else 0
    finally:
        con.close()


def silver_schema_column_names() -> set[str]:
    if not SILVER_TABLE_PATH.exists() or not DeltaTable.is_deltatable(str(SILVER_TABLE_PATH)):
        return set()
    schema = DeltaTable(str(SILVER_TABLE_PATH)).schema().to_arrow()
    return set(schema.names)


def ensure_silver_source_column() -> bool:
    """
    Add nullable source_bronze_path to existing silver if missing (metadata-only Delta op).
    Returns True if a column was added.
    """
    if not SILVER_TABLE_PATH.exists() or not DeltaTable.is_deltatable(str(SILVER_TABLE_PATH)):
        return False
    if silver_table_row_count() == 0:
        return False
    if "source_bronze_path" in silver_schema_column_names():
        return False

    dt = DeltaTable(str(SILVER_TABLE_PATH))
    dt.alter.add_columns(
        Field("source_bronze_path", PrimitiveType("string"), nullable=True)
    )
    print(f"Added source_bronze_path column to {SILVER_TABLE_PATH}", flush=True)
    return True


def _load_checkpoint_data() -> dict:
    if not CHECKPOINT_PATH.exists():
        return {}
    with open(CHECKPOINT_PATH, encoding="utf-8") as f:
        return json.load(f)


def _save_checkpoint_data(data: dict) -> None:
    CHECKPOINT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CHECKPOINT_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def load_merged_hashes() -> dict[str, str]:
    """Bronze file_path -> row_hash last seen when successfully merged to silver."""
    data = _load_checkpoint_data()
    raw = data.get("merged_hashes", {})
    return {str(k): str(v) for k, v in raw.items()}


def get_bronze_json_path_meta() -> dict[str, dict]:
    """
    Per collector JSON in bronze: content hash and latest ingested_at.
    One bronze row per JSON file (re-ingest overwrites path in practice).
    """
    if not BRONZE_TABLE_PATH.exists() or not DeltaTable.is_deltatable(
        str(BRONZE_TABLE_PATH)
    ):
        return {}

    dt = DeltaTable(str(BRONZE_TABLE_PATH))
    table = dt.to_pyarrow_table(columns=["file_path", "row_hash", "ingested_at"])

    meta: dict[str, dict] = {}
    for file_path, row_hash, ingested_at in zip(
        table.column("file_path").to_pylist(),
        table.column("row_hash").to_pylist(),
        table.column("ingested_at").to_pylist(),
    ):
        if not isinstance(file_path, str) or not file_path.endswith(JSON_BRONZE_SUFFIX):
            continue
        ts = ingested_at
        if hasattr(ts, "tzinfo") and ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        prev = meta.get(file_path)
        if prev is None or (ts is not None and ts >= prev.get("ingested_at", ts)):
            meta[file_path] = {
                "row_hash": str(row_hash),
                "ingested_at": ts,
            }
    return meta


def get_json_bronze_paths_from_delta() -> set[str]:
    return set(get_bronze_json_path_meta().keys())


def silver_table_row_count() -> int:
    if not SILVER_TABLE_PATH.exists() or not DeltaTable.is_deltatable(str(SILVER_TABLE_PATH)):
        return 0
    return DeltaTable(str(SILVER_TABLE_PATH)).to_pyarrow_dataset().count_rows()


def _parse_ingest_date(value: str) -> datetime:
    """Parse YYYY-MM-DD as UTC start of that calendar day."""
    day = datetime.strptime(value, "%Y-%m-%d").date()
    return datetime(day.year, day.month, day.day, tzinfo=timezone.utc)


def get_pending_bronze_paths(
    *,
    ingested_on_or_after: datetime | None = None,
) -> set[str]:
    """
    Paths whose bronze content hash differs from the last successful silver merge.
    """
    current = get_bronze_json_path_meta()
    merged = load_merged_hashes()
    pending = {
        path
        for path, info in current.items()
        if merged.get(path) != info["row_hash"]
    }
    if ingested_on_or_after is not None:
        pending = {
            path
            for path in pending
            if current[path]["ingested_at"] is not None
            and current[path]["ingested_at"] >= ingested_on_or_after
        }

    data = _load_checkpoint_data()
    if pending == set() and data.get("processed_file_paths") and not merged:
        print(
            "WARNING: checkpoint has processed_file_paths but no merged_hashes.\n"
            "This usually means --bootstrap-checkpoint ran after new bronze ingest\n"
            "but before silver merge. Fix:\n"
            f"    {format_command(CREATE_SILVER_LAYER, '--clear-checkpoint')}\n"
            f"    {format_command(CREATE_SILVER_LAYER, '--bootstrap-checkpoint', '--exclude-ingested-on-or-after', 'YYYY-MM-DD')}\n"
            f"    {format_command(CREATE_SILVER_LAYER, '--only-new-paths')}\n"
            "Or run --show-pending after --clear-checkpoint."
        )
    return pending


def show_pending_paths(
    *,
    ingested_on_or_after: datetime | None = None,
) -> set[str]:
    current = get_bronze_json_path_meta()
    merged = load_merged_hashes()
    pending = get_pending_bronze_paths(ingested_on_or_after=ingested_on_or_after)

    print(f"Bronze JSON paths: {len(current)}")
    print(f"Checkpoint hashes: {len(merged)}")
    if ingested_on_or_after is not None:
        print(f"Filter: ingested_at >= {ingested_on_or_after.isoformat()}")
    print(f"Pending for silver merge: {len(pending)}")

    by_day: dict[str, list[str]] = {}
    for path in pending:
        ts = current[path]["ingested_at"]
        day = ts.date().isoformat() if ts is not None else "unknown"
        by_day.setdefault(day, []).append(path)

    for day in sorted(by_day):
        print(f"\n  [{day}] ({len(by_day[day])} path(s))")
        for path in sorted(by_day[day]):
            info = current[path]
            print(f"    - {path}")
            print(f"        hash={info['row_hash'][:12]}... ingested_at={info['ingested_at']}")
    return pending


def clear_checkpoint() -> None:
    if CHECKPOINT_PATH.exists():
        CHECKPOINT_PATH.unlink()
        print(f"Removed {CHECKPOINT_PATH}")
    else:
        print("No checkpoint file to remove.")


def bootstrap_checkpoint(
    *,
    exclude_ingested_within_hours: float | None = None,
    exclude_ingested_on_or_after: datetime | None = None,
) -> None:
    """
  Mark bronze JSON paths as already reflected in silver.

  Run only when silver already contains those paths. If you ingested NEW recoleta
  JSON into bronze first, use --exclude-ingested-on-or-after so recent paths stay
  pending for --only-new-paths.
    """
    meta = get_bronze_json_path_meta()
    cutoff_hours = None
    if exclude_ingested_within_hours is not None:
        cutoff_hours = datetime.now(timezone.utc) - timedelta(
            hours=exclude_ingested_within_hours
        )

    merged_hashes: dict[str, str] = {}
    skipped = 0
    for path, info in sorted(meta.items()):
        ingested_at = info.get("ingested_at")
        if cutoff_hours is not None and ingested_at is not None and ingested_at >= cutoff_hours:
            skipped += 1
            continue
        if (
            exclude_ingested_on_or_after is not None
            and ingested_at is not None
            and ingested_at >= exclude_ingested_on_or_after
        ):
            skipped += 1
            continue
        merged_hashes[path] = info["row_hash"]

    _save_checkpoint_data(
        {
            "merged_hashes": merged_hashes,
            "processed_file_paths": sorted(merged_hashes.keys()),
            "bootstrapped_at": datetime.now(timezone.utc).isoformat(),
            "exclude_ingested_within_hours": exclude_ingested_within_hours,
            "exclude_ingested_on_or_after": (
                exclude_ingested_on_or_after.isoformat()
                if exclude_ingested_on_or_after
                else None
            ),
        }
    )
    print(
        f"Checkpoint: {len(merged_hashes)} path(s) marked as merged, "
        f"{skipped} path(s) left pending"
    )
    print(f"  {CHECKPOINT_PATH}")


def update_checkpoint_after_merge(paths: set[str]) -> None:
    meta = get_bronze_json_path_meta()
    merged = load_merged_hashes()
    for path in paths:
        if path in meta:
            merged[path] = meta[path]["row_hash"]
    data = _load_checkpoint_data()
    data["merged_hashes"] = merged
    data["processed_file_paths"] = sorted(merged.keys())
    data["last_merge_at"] = datetime.now(timezone.utc).isoformat()
    _save_checkpoint_data(data)


def merge_chunks_to_silver(
    dedupe_path: Path,
    *,
    z_order_before_merge: bool = False,
    compact_after: bool = True,
) -> None:
    """Stream deduped Parquet into Delta without loading the full table in pandas."""
    pf = pq.ParquetFile(dedupe_path)
    silver_exists = DeltaTable.is_deltatable(str(SILVER_TABLE_PATH))

    if (
        z_order_before_merge
        and silver_exists
        and DeltaTable.is_deltatable(str(SILVER_TABLE_PATH))
    ):
        print("Optimizing Silver (Z-order on (id_processo, decisao))...")
        DeltaTable(str(SILVER_TABLE_PATH)).optimize.z_order(["id_processo", "decisao"])

    n_batches = (pf.metadata.num_rows + MERGE_BATCH_ROWS - 1) // MERGE_BATCH_ROWS
    dt_silver = (
        DeltaTable(str(SILVER_TABLE_PATH))
        if silver_exists and DeltaTable.is_deltatable(str(SILVER_TABLE_PATH))
        else None
    )
    for batch in tqdm(
        pf.iter_batches(batch_size=MERGE_BATCH_ROWS),
        desc="Merge → Silver",
        unit="batch",
        total=max(n_batches, 1),
    ):
        df_chunk = batch.to_pandas()
        if df_chunk.empty:
            continue
        if not silver_exists:
            write_deltalake(str(SILVER_TABLE_PATH), df_chunk, mode="overwrite")
            silver_exists = True
            dt_silver = DeltaTable(str(SILVER_TABLE_PATH))
        else:
            assert dt_silver is not None
            (
                dt_silver.merge(
                    source=df_chunk,
                    predicate=(
                        "target.id_processo = source.id_processo "
                        "AND target.decisao = source.decisao"
                    ),
                    source_alias="source",
                    target_alias="target",
                )
                .when_matched_update_all()
                .when_not_matched_insert_all()
                .execute()
            )
        del df_chunk, batch
        gc.collect()

    del pf, dt_silver
    gc.collect()

    if (
        compact_after
        and silver_exists
        and DeltaTable.is_deltatable(str(SILVER_TABLE_PATH))
    ):
        print("Optimizing Silver Delta files (compact)...")
        DeltaTable(str(SILVER_TABLE_PATH)).optimize.compact()


def publish_silver_for_sources(
    dedupe_path: Path,
    source_paths: set[str],
    *,
    z_order_before_merge: bool = False,
    compact_after: bool = True,
) -> None:
    """Delete rows per source_bronze_path, then append deduped parquet (no full-table MERGE)."""
    ensure_silver_source_column()
    pf = pq.ParquetFile(dedupe_path)
    silver_exists = DeltaTable.is_deltatable(str(SILVER_TABLE_PATH))

    if (
        z_order_before_merge
        and silver_exists
        and DeltaTable.is_deltatable(str(SILVER_TABLE_PATH))
    ):
        print("Optimizing Silver (Z-order on (id_processo, decisao))...")
        DeltaTable(str(SILVER_TABLE_PATH)).optimize.z_order(["id_processo", "decisao"])

    if silver_exists:
        dt_silver = DeltaTable(str(SILVER_TABLE_PATH))
        for source in sorted(source_paths):
            _validate_collector_path(source)
            escaped = _escape_sql_literal(source)
            print(f"Deleting silver rows for source: {source}", flush=True)
            dt_silver.delete(f"source_bronze_path = '{escaped}'")
        del dt_silver
        gc.collect()

    n_batches = (pf.metadata.num_rows + PUBLISH_BATCH_ROWS - 1) // PUBLISH_BATCH_ROWS
    for batch in tqdm(
        pf.iter_batches(batch_size=PUBLISH_BATCH_ROWS),
        desc="Append → Silver",
        unit="batch",
        total=max(n_batches, 1),
    ):
        if batch.num_rows == 0:
            continue
        arrow_table = pa.Table.from_batches([batch])
        if not silver_exists:
            write_deltalake(str(SILVER_TABLE_PATH), arrow_table, mode="overwrite")
            silver_exists = True
        else:
            write_deltalake(
                str(SILVER_TABLE_PATH),
                arrow_table,
                mode="append",
                schema_mode="merge",
            )
        del arrow_table, batch
        gc.collect()

    del pf
    gc.collect()

    if (
        compact_after
        and silver_exists
        and DeltaTable.is_deltatable(str(SILVER_TABLE_PATH))
    ):
        print("Optimizing Silver Delta files (compact)...")
        DeltaTable(str(SILVER_TABLE_PATH)).optimize.compact()


DEFAULT_COMPACT_BUCKETS = 64


def _compact_order_clause(col_names: set[str]) -> str:
    order_parts: list[str] = []
    if "source_bronze_path" in col_names:
        order_parts.append("CASE WHEN source_bronze_path IS NULL THEN 1 ELSE 0 END")
    if "ingested_at" in col_names:
        order_parts.append("ingested_at DESC NULLS LAST")
    elif "data_disponibilizacao" in col_names:
        order_parts.append("data_disponibilizacao DESC NULLS LAST")
    if not order_parts:
        if "cd_processo" in col_names:
            order_parts.append("cd_processo DESC NULLS LAST")
        else:
            order_parts.append("id_processo DESC NULLS LAST")
    return ", ".join(order_parts)


def _compact_duckdb_connect() -> duckdb.DuckDBPyConnection:
    """Alias for global compact (same lake DuckDB settings as staging dedupe)."""
    return _lake_duckdb_connect()


def _rewrite_deduped_parquet_to_silver(
    dedupe_dir: Path,
    *,
    compact_after: bool,
) -> int:
    """Stream deduped parquet files into silver Delta. Returns rows written."""
    paths = sorted(dedupe_dir.glob("deduped_*.parquet"))
    if not paths:
        print("No deduped parquet files to rewrite.")
        return 0

    total_rows = 0
    first = True
    for parquet_path in paths:
        pf = pq.ParquetFile(parquet_path)
        total_rows += pf.metadata.num_rows or 0
        n_batches = (pf.metadata.num_rows + PUBLISH_BATCH_ROWS - 1) // PUBLISH_BATCH_ROWS
        for batch in tqdm(
            pf.iter_batches(batch_size=PUBLISH_BATCH_ROWS),
            desc=f"Rewrite {parquet_path.name}",
            unit="batch",
            total=max(n_batches, 1),
            leave=False,
        ):
            if batch.num_rows == 0:
                continue
            arrow_table = pa.Table.from_batches([batch])
            if first:
                write_deltalake(str(SILVER_TABLE_PATH), arrow_table, mode="overwrite")
                first = False
            else:
                write_deltalake(
                    str(SILVER_TABLE_PATH),
                    arrow_table,
                    mode="append",
                    schema_mode="merge",
                )
            del arrow_table, batch
            gc.collect()
        del pf

    if compact_after and DeltaTable.is_deltatable(str(SILVER_TABLE_PATH)):
        print("Compacting silver after global dedupe...")
        DeltaTable(str(SILVER_TABLE_PATH)).optimize.compact()
    return total_rows


def _silver_parquet_read_expr(
    *,
    max_files: int | None = None,
) -> str:
    """SQL fragment: read_parquet(...) for silver data files (optional cap for fast tests)."""
    files = sorted(
        p
        for p in SILVER_TABLE_PATH.rglob("*.parquet")
        if p.name != "_delta_log" and "metadata" not in p.parts
    )
    if not files:
        raise FileNotFoundError(f"No parquet files under {SILVER_TABLE_PATH}")
    if max_files is not None:
        files = files[:max_files]
    if len(files) == 1:
        return f"read_parquet('{_sql_path_literal(files[0])}')"
    paths_sql = ", ".join(f"'{_sql_path_literal(p)}'" for p in files)
    return f"read_parquet([{paths_sql}], union_by_name=true)"


def compact_silver_global_dedupe(
    *,
    compact_after: bool = True,
    num_buckets: int = DEFAULT_COMPACT_BUCKETS,
    max_buckets: int | None = None,
    skip_rewrite: bool = False,
    sample_permille: int | None = None,
    max_parquet_files: int | None = None,
) -> None:
    """
    One-off: dedupe full silver on (id_processo, decisao), preferring rows with
    source_bronze_path when present. Uses hash buckets to avoid DuckDB OOM on wide text.

    Fast validation: compact_silver_processos.py --test (1% sample, 4 buckets, no rewrite).
    """
    if not SILVER_TABLE_PATH.exists() or not DeltaTable.is_deltatable(str(SILVER_TABLE_PATH)):
        print("Silver table empty or missing; nothing to compact.")
        return

    if num_buckets < 1:
        raise ValueError("num_buckets must be >= 1")

    schema = DeltaTable(str(SILVER_TABLE_PATH)).schema().to_arrow()
    col_names = set(schema.names)
    if "id_processo" not in col_names or "decisao" not in col_names:
        raise ValueError(f"Silver missing id_processo/decisao columns: {sorted(col_names)}")

    order_clause = _compact_order_clause(col_names)
    buckets_to_run = num_buckets if max_buckets is None else min(max_buckets, num_buckets)

    run_dir = TMP_STAGE_DIR / f"compact_{uuid.uuid4().hex}"
    shard_dir = run_dir / "sharded"
    dedupe_dir = run_dir / "deduped"
    shard_dir.mkdir(parents=True, exist_ok=True)
    dedupe_dir.mkdir(parents=True, exist_ok=True)

    read_expr = _silver_parquet_read_expr(max_files=max_parquet_files)
    bucket_expr = f"(abs(hash(CAST(id_processo AS VARCHAR))) % {num_buckets})"

    sample_filter = ""
    if sample_permille is not None:
        if not 1 <= sample_permille <= 999:
            raise ValueError("sample_permille must be between 1 and 999")
        sample_filter = (
            f" AND (abs(hash(CAST(id_processo AS VARCHAR) || 'sample')) % 1000) "
            f"< {sample_permille}"
        )

    row_count_before = silver_table_row_count()
    print(
        f"Compact: {row_count_before:,} rows in silver | buckets={num_buckets} "
        f"| run={buckets_to_run}"
        + (f" | sample~{sample_permille / 10:.1f}%" if sample_permille else "")
        + (f" | parquet_files<={max_parquet_files}" if max_parquet_files else "")
        + (" | TEST (skip rewrite)" if skip_rewrite else "")
    )
    print(f"Order clause: {order_clause}")

    con = _compact_duckdb_connect()
    try:
        print("Phase 1/3: shard silver into buckets (single scan)...", flush=True)
        con.execute(
            f"""
            COPY (
                SELECT
                    *,
                    {bucket_expr}::INTEGER AS _bucket
                FROM {read_expr}
                WHERE id_processo IS NOT NULL
                {sample_filter}
            )
            TO '{_sql_path_literal(shard_dir)}'
            (FORMAT PARQUET, PARTITION_BY (_bucket), COMPRESSION ZSTD)
            """
        )

        print(f"Phase 2/3: dedupe {buckets_to_run} bucket(s)...", flush=True)
        for bucket in tqdm(range(buckets_to_run), desc="Dedupe buckets", unit="bucket"):
            shard_glob = _sql_path_literal(shard_dir / f"_bucket={bucket}" / "*.parquet")
            out_file = dedupe_dir / f"deduped_{bucket:04d}.parquet"
            shard_path = shard_dir / f"_bucket={bucket}"
            if not shard_path.exists():
                continue
            con.execute(
                f"""
                COPY (
                    SELECT * EXCLUDE (rn, _bucket) FROM (
                        SELECT
                            *,
                            ROW_NUMBER() OVER (
                                PARTITION BY id_processo, decisao
                                ORDER BY {order_clause}
                            ) AS rn
                        FROM read_parquet('{shard_glob}', union_by_name=true)
                    )
                    WHERE rn = 1
                )
                TO '{_sql_path_literal(out_file)}'
                (FORMAT PARQUET, COMPRESSION ZSTD)
                """
            )

        deduped_files = sorted(dedupe_dir.glob("deduped_*.parquet"))
        deduped_rows = sum(pq.ParquetFile(p).metadata.num_rows or 0 for p in deduped_files)
        print(
            f"Deduped: {deduped_rows:,} rows in {len(deduped_files)} file(s) "
            f"(from ~{row_count_before:,} input rows)"
        )

        if skip_rewrite:
            print(f"TEST OK — rewrite skipped. Staging left at {run_dir}")
            return

        print("Phase 3/3: rewrite silver Delta...", flush=True)
        written = _rewrite_deduped_parquet_to_silver(
            dedupe_dir,
            compact_after=compact_after,
        )
        print(f"Global dedupe complete: {written:,} rows at {SILVER_TABLE_PATH}")
    finally:
        con.close()
        if not skip_rewrite:
            shutil.rmtree(run_dir, ignore_errors=True)
        else:
            print(f"Remove test staging manually when done: {run_dir}")


def process_bronze_to_silver(
    *,
    only_new_paths: bool = False,
    paths: set[str] | None = None,
    ingested_on_or_after: datetime | None = None,
    z_order_before_merge: bool = False,
    compact_after: bool = False,
    legacy_merge: bool = False,
    stage_only: bool = False,
) -> set[str]:
    """
    Transform bronze JSON rows into silver processos Delta.

    Returns the set of bronze file_path values processed in this run (empty if none).
  """
    if not BRONZE_TABLE_PATH.exists():
        print(f"Bronze table not found at {BRONZE_TABLE_PATH}")
        return set()

    paths_to_process: set[str] | None = None
    if paths is not None:
        paths_to_process = set(paths)
        if not paths_to_process:
            return set()
    elif only_new_paths:
        if not load_merged_hashes() and silver_table_row_count() > 0:
            print(
                "Silver has data but no merged_hashes checkpoint.\n"
                "If silver already reflects old bronze (before recoleta), run:\n"
                "  --bootstrap-checkpoint --exclude-ingested-on-or-after 2026-05-28\n"
                "Use --show-pending to inspect before merging."
            )
            return set()

        paths_to_process = get_pending_bronze_paths(
            ingested_on_or_after=ingested_on_or_after,
        )
        if not paths_to_process:
            print(
                "No bronze JSON paths need silver merge (hashes match checkpoint).\n"
                "Use --show-pending to list details."
            )
            return set()

    if paths_to_process is None:
        paths_to_process = get_json_bronze_paths_from_delta()

    if legacy_merge and silver_table_row_count() > 0:
        print(
            "WARNING: --legacy-merge against existing silver is slow and high RAM; "
            "emergency use only.",
            file=sys.stderr,
        )

    if paths_to_process is not None:
        print(f"Silver publish: {len(paths_to_process)} collector JSON path(s).")
        for p in sorted(paths_to_process):
            print(f"  - {p}")

    TMP_STAGE_DIR.mkdir(parents=True, exist_ok=True)
    run_stage_dir = TMP_STAGE_DIR / f"run_{uuid.uuid4().hex}"
    run_stage_dir.mkdir(parents=True, exist_ok=True)
    print(f"Staging directory: {run_stage_dir}")

    print(
        f"Phase 1: Stage from sqlite/json (item flush={ITEM_FLUSH_THRESHOLD})..."
    )
    if not _stage_collector_paths(paths_to_process, run_stage_dir):
        print("No valid items found to stage.")
        shutil.rmtree(run_stage_dir, ignore_errors=True)
        return set()

    print("\nPhase 2: Local dedupe (DuckDB) → publish to silver...")
    staged_files = sorted(run_stage_dir.glob("stage_*.parquet"))

    if not staged_files:
        print("No valid items found to process into Silver.")
        shutil.rmtree(run_stage_dir, ignore_errors=True)
        return set()

    dedupe_path = run_stage_dir / "_deduped_for_publish.parquet"
    deduped_rows = build_deduped_parquet(run_stage_dir, dedupe_path)
    for p in staged_files:
        p.unlink(missing_ok=True)

    if stage_only:
        print(
            f"\n--stage-only: {deduped_rows:,} deduped rows at {dedupe_path}\n"
            f"  Staging dir: {run_stage_dir} (remove manually when done)"
        )
        return set()

    source_paths = set(paths_to_process)
    if legacy_merge:
        print("Using legacy full-table MERGE (--legacy-merge).")
        merge_chunks_to_silver(
            dedupe_path,
            z_order_before_merge=z_order_before_merge,
            compact_after=compact_after,
        )
    else:
        publish_silver_for_sources(
            dedupe_path,
            source_paths,
            z_order_before_merge=z_order_before_merge,
            compact_after=compact_after,
        )

    dedupe_path.unlink(missing_ok=True)
    shutil.rmtree(run_stage_dir, ignore_errors=True)
    gc.collect()

    processed_paths = paths_to_process
    if only_new_paths and processed_paths:
        update_checkpoint_after_merge(processed_paths)
        print(f"Checkpoint updated ({len(processed_paths)} path(s) published this run).")

    print(f"\nSilver Layer fully updated! Data safely stored at: {SILVER_TABLE_PATH}")
    return processed_paths if paths_to_process is not None else set()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Bronze coletas JSON → silver processos Delta."
    )
    parser.add_argument(
        "--only-new-paths",
        action="store_true",
        help=(
            "Process only bronze JSON file_path entries not yet listed in "
            f"{CHECKPOINT_PATH.name} (fast after recoleta)."
        ),
    )
    parser.add_argument(
        "--bootstrap-checkpoint",
        action="store_true",
        help=(
            "Seed checkpoint merged_hashes from bronze. Run BEFORE ingesting recoleta "
            "into bronze, or use --exclude-ingested-within-hours for recent paths."
        ),
    )
    parser.add_argument(
        "--exclude-ingested-within-hours",
        type=float,
        default=None,
        metavar="HOURS",
        help=(
            "With --bootstrap-checkpoint: do not mark JSON paths ingested to bronze "
            "within the last N hours (they stay pending for --only-new-paths)."
        ),
    )
    parser.add_argument(
        "--exclude-ingested-on-or-after",
        type=str,
        default=None,
        metavar="YYYY-MM-DD",
        help=(
            "With --bootstrap-checkpoint: do not mark paths ingested on/after this "
            "UTC date (they stay pending)."
        ),
    )
    parser.add_argument(
        "--only-ingested-on-or-after",
        type=str,
        default=None,
        metavar="YYYY-MM-DD",
        help=(
            "With --show-pending or --only-new-paths: restrict to paths ingested on/after "
            "this UTC date."
        ),
    )
    parser.add_argument(
        "--show-pending",
        action="store_true",
        help="List bronze JSON paths that still need silver merge (hash diff).",
    )
    parser.add_argument(
        "--clear-checkpoint",
        action="store_true",
        help="Delete the silver path checkpoint file.",
    )
    parser.add_argument(
        "--z-order-before-merge",
        action="store_true",
        help="Run Z-order on the full silver table before merge (slow; off by default).",
    )
    parser.add_argument(
        "--compact",
        action="store_true",
        help="Run Delta compact() after publish (off by default).",
    )
    parser.add_argument(
        "--no-compact",
        action="store_true",
        help="Deprecated alias: compact is already off unless --compact is set.",
    )
    parser.add_argument(
        "--ensure-schema",
        action="store_true",
        help="Add source_bronze_path column to silver if missing, then exit.",
    )
    parser.add_argument(
        "--stage-only",
        action="store_true",
        help="Stage + local dedupe only; no publish or checkpoint (large-file preflight).",
    )
    parser.add_argument(
        "--path",
        action="append",
        metavar="FILE_PATH",
        help="Process only this bronze file_path (repeatable).",
    )
    parser.add_argument(
        "--legacy-merge",
        action="store_true",
        help="Use full-table Delta MERGE instead of delete-by-source + append.",
    )
    parser.add_argument(
        "--compact-global-dedupe",
        action="store_true",
        help="Run one-off global dedupe on silver (id_processo, decisao); not for nightly runs.",
    )
    return parser.parse_args()


def _parse_optional_ingest_date(raw: str | None) -> datetime | None:
    if raw is None:
        return None
    return _parse_ingest_date(raw)


def _compact_after_from_args(args: argparse.Namespace) -> bool:
    return bool(args.compact) and not args.no_compact


if __name__ == "__main__":
    args = parse_args()
    only_ingested_after = _parse_optional_ingest_date(args.only_ingested_on_or_after)
    compact_after = _compact_after_from_args(args)

    if args.ensure_schema:
        ensure_silver_source_column()
    elif args.clear_checkpoint:
        clear_checkpoint()
    elif args.compact_global_dedupe:
        compact_silver_global_dedupe(compact_after=compact_after)
    elif args.show_pending:
        show_pending_paths(ingested_on_or_after=only_ingested_after)
    elif args.bootstrap_checkpoint:
        exclude_date = _parse_optional_ingest_date(args.exclude_ingested_on_or_after)
        bootstrap_checkpoint(
            exclude_ingested_within_hours=args.exclude_ingested_within_hours,
            exclude_ingested_on_or_after=exclude_date,
        )
    elif args.path:
        process_bronze_to_silver(
            only_new_paths=True,
            paths=set(args.path),
            z_order_before_merge=args.z_order_before_merge,
            compact_after=compact_after,
            legacy_merge=args.legacy_merge,
            stage_only=args.stage_only,
        )
    elif args.only_new_paths:
        process_bronze_to_silver(
            only_new_paths=True,
            ingested_on_or_after=only_ingested_after,
            z_order_before_merge=args.z_order_before_merge,
            compact_after=compact_after,
            legacy_merge=args.legacy_merge,
            stage_only=args.stage_only,
        )
    else:
        process_bronze_to_silver(
            only_new_paths=False,
            z_order_before_merge=args.z_order_before_merge,
            compact_after=compact_after,
            legacy_merge=args.legacy_merge,
            stage_only=args.stage_only,
        )
        if not CHECKPOINT_PATH.exists():
            update_checkpoint_after_merge(get_json_bronze_paths_from_delta())
            print(f"Checkpoint written for future --only-new-paths runs: {CHECKPOINT_PATH}")
