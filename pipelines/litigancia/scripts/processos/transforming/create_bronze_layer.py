import gc
import json
import sqlite3
import hashlib
from pathlib import Path
from datetime import datetime, timezone
import pandas as pd
from deltalake import write_deltalake, DeltaTable
from tqdm import tqdm

from config.paths import AGGREGATED_DB, BRONZE_COLETAS, COLLECT_ROOT

DELTA_TABLE_PATH = BRONZE_COLETAS
DB_PATH = AGGREGATED_DB

DB_CHUNK_SIZE = 25000

JSON_SUFFIX = "process_grouped_all_assuntos.json"


def _escape_sql_literal(value: str) -> str:
    return value.replace("'", "''")


def _validate_collector_path(path_str: str) -> Path:
    if "'" in path_str:
        raise ValueError(f"file_path contains invalid character for delete: {path_str}")
    resolved = Path(path_str).resolve()
    collect_root = COLLECT_ROOT.resolve()
    if not resolved.is_relative_to(collect_root):
        raise ValueError(f"file_path must be under COLLECT_ROOT: {path_str}")
    return resolved


def get_processed_paths() -> set:
    """All file_path values present in bronze (JSON + sqlite virtual paths)."""
    if not DELTA_TABLE_PATH.exists() or not DeltaTable.is_deltatable(str(DELTA_TABLE_PATH)):
        return set()

    try:
        dt = DeltaTable(str(DELTA_TABLE_PATH))
        unique_paths = dt.to_pyarrow_table(columns=["file_path"]).column("file_path").unique().to_pylist()
        return set(unique_paths)
    except Exception as e:
        print(f"Warning: Delta table state could not be read: {e}")
        return set()


def get_bronze_json_hashes() -> dict[str, str]:
    """Latest row_hash per collector JSON path in bronze."""
    if not DELTA_TABLE_PATH.exists() or not DeltaTable.is_deltatable(str(DELTA_TABLE_PATH)):
        return {}

    try:
        dt = DeltaTable(str(DELTA_TABLE_PATH))
        table = dt.to_pyarrow_table(columns=["file_path", "row_hash", "ingested_at"])
    except Exception as e:
        print(f"Warning: could not read bronze JSON hashes: {e}")
        return {}

    latest: dict[str, str] = {}
    latest_ts: dict[str, datetime] = {}
    for file_path, row_hash, ingested_at in zip(
        table.column("file_path").to_pylist(),
        table.column("row_hash").to_pylist(),
        table.column("ingested_at").to_pylist(),
    ):
        if not isinstance(file_path, str) or not file_path.endswith(JSON_SUFFIX):
            continue
        ts = ingested_at
        if hasattr(ts, "tzinfo") and ts is not None and ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        prev_ts = latest_ts.get(file_path)
        if prev_ts is None or (ts is not None and ts >= prev_ts):
            latest[file_path] = str(row_hash)
            if ts is not None:
                latest_ts[file_path] = ts
    return latest


def get_json_hash_for_path(path_str: str) -> str | None:
    """Latest row_hash for one collector JSON path (avoids loading all bronze hashes)."""
    if not DELTA_TABLE_PATH.exists() or not DeltaTable.is_deltatable(str(DELTA_TABLE_PATH)):
        return None

    path_str = str(Path(path_str).resolve())
    try:
        dt = DeltaTable(str(DELTA_TABLE_PATH))
        table = dt.to_pyarrow_table(
            columns=["file_path", "row_hash", "ingested_at"],
            filters=[("file_path", "=", path_str)],
        )
    except Exception as e:
        print(f"Warning: could not read bronze hash for {path_str}: {e}")
        return None

    if table.num_rows == 0:
        return None

    latest_hash: str | None = None
    latest_ts: datetime | None = None
    for file_path, row_hash, ingested_at in zip(
        table.column("file_path").to_pylist(),
        table.column("row_hash").to_pylist(),
        table.column("ingested_at").to_pylist(),
    ):
        if file_path != path_str:
            continue
        ts = ingested_at
        if hasattr(ts, "tzinfo") and ts is not None and ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        if latest_ts is None or (ts is not None and ts >= latest_ts):
            latest_hash = str(row_hash)
            if ts is not None:
                latest_ts = ts
    return latest_hash


def write_to_bronze(
    df_batch: pd.DataFrame,
    *,
    merge_on: str = "path_and_hash",
) -> None:
    """
    merge_on:
      - path_and_hash: sqlite chunks (same path+hash = update)
      - file_path: collector JSON (replace row when file content changes)
      - append: first write only
    """
    if not DeltaTable.is_deltatable(str(DELTA_TABLE_PATH)):
        write_deltalake(str(DELTA_TABLE_PATH), df_batch, mode="append")
        return

    if merge_on == "append":
        write_deltalake(str(DELTA_TABLE_PATH), df_batch, mode="append", schema_mode="merge")
        return

    predicate = (
        "target.file_path = source.file_path"
        if merge_on == "file_path"
        else "target.file_path = source.file_path AND target.row_hash = source.row_hash"
    )
    dt = DeltaTable(str(DELTA_TABLE_PATH))
    (
        dt.merge(
            source=df_batch,
            predicate=predicate,
            source_alias="source",
            target_alias="target",
        )
        .when_matched_update_all()
        .when_not_matched_insert_all()
        .execute()
    )


def replace_json_bronze_row(record: dict) -> None:
    """Replace one collector JSON row: delete by file_path, append metadata (no MERGE scan)."""
    path_str = record["file_path"]
    _validate_collector_path(path_str)
    escaped = _escape_sql_literal(path_str)

    if DeltaTable.is_deltatable(str(DELTA_TABLE_PATH)):
        dt = DeltaTable(str(DELTA_TABLE_PATH))
        dt.delete(f"file_path = '{escaped}'")

    write_deltalake(
        str(DELTA_TABLE_PATH),
        pd.DataFrame([record]),
        mode="append",
        schema_mode="merge",
    )


def file_content_hash(file_path: Path) -> str | None:
    """MD5 of file bytes without loading the whole file into a single str first."""
    digest = hashlib.md5()
    try:
        with open(file_path, "rb") as f:
            while chunk := f.read(8 * 1024 * 1024):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None


def find_pending_json_files(
    bronze_hashes: dict[str, str] | None = None,
    *,
    show_progress: bool = False,
) -> list[Path]:
    """New or changed collector JSONs vs latest hash in bronze."""
    if bronze_hashes is None:
        print("Loading bronze JSON hashes...", flush=True)
        bronze_hashes = get_bronze_json_hashes()

    all_json = list(COLLECT_ROOT.rglob(JSON_SUFFIX))
    pending: list[Path] = []

    for i, file_path in enumerate(all_json, 1):
        if show_progress and (i == 1 or i % 5 == 0 or i == len(all_json)):
            print(f"  Scanning disk JSON {i}/{len(all_json)}...", flush=True)
        path_str = str(file_path)
        content_hash = file_content_hash(file_path)
        if content_hash is None:
            continue
        if bronze_hashes.get(path_str) != content_hash:
            pending.append(file_path)

    return pending


def ingest_json_file(
    file_path: Path,
    bronze_hashes: dict[str, str] | None = None,
) -> bool:
    """
    Ingest a single collector JSON into bronze. Returns True if a write was performed.
    Metadata only (hash via chunked read); silver reads JSON from disk.
    """
    path_str = str(file_path.resolve())

    content_hash = file_content_hash(file_path)
    if content_hash is None:
        return False

    if bronze_hashes is None:
        existing = get_json_hash_for_path(path_str)
        bronze_hashes = {path_str: existing} if existing is not None else {}
    if bronze_hashes.get(path_str) == content_hash:
        return False

    record = {
        "file_name": file_path.name,
        "file_path": path_str,
        "row_hash": content_hash,
        "ingested_at": datetime.now(timezone.utc),
    }
    replace_json_bronze_row(record)
    bronze_hashes[path_str] = content_hash
    del record
    gc.collect()
    print(f"Bronze: ingested {path_str}", flush=True)
    return True


def ingest_jsons() -> int:
    print("\n--- Phase 1: Ingesting JSON Files ---")
    all_json_files = list(COLLECT_ROOT.rglob(JSON_SUFFIX))
    pending_files = find_pending_json_files()

    if not pending_files:
        print(f"All {len(all_json_files)} JSON files match bronze (no new or changed files).")
        return 0

    print(f"Found {len(pending_files)} new/changed JSON file(s) (of {len(all_json_files)} on disk).")

    progress_bar = tqdm(pending_files, desc="JSONs", unit="file")
    written = 0
    for file_path in progress_bar:
        try:
            if ingest_json_file(file_path):
                written += 1
        except Exception as e:
            tqdm.write(f"Error ingesting {file_path}: {e}")

    return written


def ingest_sqlite(processed_paths: set) -> int:
    print(f"\n--- Phase 2: Ingesting SQLite Database ---")
    if not DB_PATH.exists():
        print(f"Database not found at {DB_PATH}")
        return 0

    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
    tables = [row[0] for row in cursor.fetchall()]
    
    pending_tables = []
    for table in tables:
        virtual_path = f"{str(DB_PATH)}|{table}"
        if virtual_path not in processed_paths:
            pending_tables.append((table, virtual_path))

    if not pending_tables:
        print(f"All {len(tables)} DB tables are already fully ingested.")
        conn.close()
        return 0

    print(f"Found {len(pending_tables)} pending tables.")

    for table_name, virtual_path in pending_tables:
        tqdm.write(f"\nProcessing table: {table_name}")
        
        cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
        total_rows = cursor.fetchone()[0]
        
        if total_rows == 0:
            continue

        progress_bar = tqdm(total=total_rows, desc=table_name, unit="row")
        
        # Read and process in chunks to prevent memory crashes
        for chunk_df in pd.read_sql_query(f"SELECT * FROM {table_name}", conn, chunksize=DB_CHUNK_SIZE):
            records = []
            
            print(f"Processing chunk with {len(chunk_df)} rows")
            
            for record in chunk_df.to_dict(orient="records"):
                row_json = json.dumps(record, ensure_ascii=False)
                row_hash = hashlib.md5(row_json.encode('utf-8')).hexdigest()
                
                records.append({
                    "file_name": DB_PATH.name,
                    "file_path": virtual_path,
                    "row_hash": row_hash,
                    "raw_data": row_json,
                    "ingested_at": datetime.now(timezone.utc),
                })
            
            if records:
                write_to_bronze(pd.DataFrame(records), merge_on="path_and_hash")

            progress_bar.update(len(chunk_df))

        progress_bar.close()

    conn.close()
    return len(pending_tables)


def run_bronze_pipeline(*, ingest_sqlite_db: bool = True) -> dict[str, int]:
    """Ingest new/changed JSONs and optional sqlite tables into bronze."""
    print("Initializing Bronze Layer Pipeline...")
    json_count = ingest_jsons()
    db_count = 0
    if ingest_sqlite_db:
        existing_paths = get_processed_paths()
        db_count = ingest_sqlite(existing_paths)
    print(f"\nBronze pipeline complete. Data at: {DELTA_TABLE_PATH}")
    return {"json_files": json_count, "sqlite_tables": db_count}


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Ingest collector JSON/SQLite into bronze Delta.")
    parser.add_argument(
        "--skip-sqlite",
        action="store_true",
        help="Only ingest process_grouped_all_assuntos.json files.",
    )
    args = parser.parse_args()
    run_bronze_pipeline(ingest_sqlite_db=not args.skip_sqlite)