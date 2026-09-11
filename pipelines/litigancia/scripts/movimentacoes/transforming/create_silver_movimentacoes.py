#!/usr/bin/env python3
"""Bronze face movimentações → silver movimentacoes_delta (incremental, resumable)."""

from __future__ import annotations

import argparse
import gc
import hashlib
import json
import sys
from datetime import datetime, timezone

import duckdb
import pandas as pd
from deltalake import DeltaTable, write_deltalake
from tqdm import tqdm

from config.paths import BRONZE_FACE, LAKE_ROOT, SILVER_MOVIMENTACOES

BRONZE_FACE_TABLE = BRONZE_FACE
SILVER_MOV_TABLE = SILVER_MOVIMENTACOES
STATS_PATH = LAKE_ROOT / "silver_layer" / ".movimentacoes_silver_stats.json"

# M1 + USB defaults: small fetches, frequent commits, chunked writes.
PARENT_BATCH_SIZE = 500
EXPLODED_RAM_THRESHOLD = 25000
FLUSH_EVERY_PARENTS = 100
MERGE_CHUNK_SIZE = 5000
TAIL_THRESHOLD = 5000

METADATA_COLS = [
    "cd_processo",
    "numero",
    "classe",
    "assunto",
    "foro",
    "vara",
    "controle",
    "area",
    "autores",
    "advogados_autores",
    "reus",
    "advogados_reus",
]


def _escape_sql_literal(value: str) -> str:
    return value.replace("'", "''")


def _silver_readable() -> bool:
    return SILVER_MOV_TABLE.exists() and DeltaTable.is_deltatable(str(SILVER_MOV_TABLE))


def _duckdb_connect() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.execute("SET threads=1")
    con.execute("SET preserve_insertion_order=false")
    con.execute("SET memory_limit='3GB'")
    con.execute("SET temp_directory='/tmp'")
    return con


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


def _count_silver_parents_live() -> int:
    if not _silver_readable():
        return 0
    silver_lit = _escape_sql_literal(str(SILVER_MOV_TABLE))
    con = _duckdb_connect()
    try:
        row = con.execute(
            f"""
            SELECT count(DISTINCT cd_processo)
            FROM delta_scan('{silver_lit}')
            WHERE cd_processo IS NOT NULL
            """
        ).fetchone()
        return int(row[0]) if row else 0
    finally:
        con.close()


def _count_pending_unique_except() -> int:
    if not BRONZE_FACE_TABLE.exists():
        return 0
    bronze_lit = _escape_sql_literal(str(BRONZE_FACE_TABLE))
    con = _duckdb_connect()
    try:
        if not _silver_readable():
            row = con.execute(
                f"""
                SELECT count(DISTINCT cd_processo)
                FROM delta_scan('{bronze_lit}')
                WHERE cd_processo IS NOT NULL
                """
            ).fetchone()
            return int(row[0]) if row else 0

        silver_lit = _escape_sql_literal(str(SILVER_MOV_TABLE))
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


def _refresh_stats_cache() -> dict:
    print("Refreshing movimentacoes stats (slow DuckDB EXCEPT over bronze + silver)...")
    pending_unique = _count_pending_unique_except()
    silver_parents = _count_silver_parents_live()
    stats = {
        "silver_unique_parents": silver_parents,
        "pending_unique": pending_unique,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    _save_stats(stats)
    return stats


def _sync_stats_after_publish(*, published_parents: int) -> None:
    stats = _load_stats() or {}
    if "pending_unique" in stats:
        stats["pending_unique"] = max(0, int(stats["pending_unique"]) - published_parents)
        stats["silver_unique_parents"] = int(stats.get("silver_unique_parents", 0)) + published_parents
    else:
        stats["pending_unique"] = _count_pending_unique_except()
        stats["silver_unique_parents"] = _count_silver_parents_live()
    stats["updated_at"] = datetime.now(timezone.utc).isoformat()
    _save_stats(stats)


def count_pending_stats(*, refresh: bool = False) -> tuple[int, int]:
    """Return (pending_unique, silver_unique_parents). Fast unless refresh=True."""
    if not BRONZE_FACE_TABLE.exists():
        return 0, 0

    if refresh or _load_stats() is None:
        stats = _refresh_stats_cache()
    else:
        stats = _load_stats()
        if "pending_unique" not in stats:
            print(
                "Computing pending count (one-time slow DuckDB query)...",
                file=sys.stderr,
            )
            stats["pending_unique"] = _count_pending_unique_except()
            stats["silver_unique_parents"] = _count_silver_parents_live()
            stats["updated_at"] = datetime.now(timezone.utc).isoformat()
            _save_stats(stats)

    return int(stats["pending_unique"]), int(stats.get("silver_unique_parents", 0))


def fetch_pending_parents_batch(
    limit: int,
    *,
    exclude_cds: set[str] | None = None,
) -> pd.DataFrame:
    """Fetch only pending bronze parents (DuckDB anti-join). Used for tail runs."""
    bronze_lit = _escape_sql_literal(str(BRONZE_FACE_TABLE))
    exclude_filter = ""
    if exclude_cds:
        literals = ", ".join(f"'{_escape_sql_literal(cd)}'" for cd in exclude_cds)
        exclude_filter = f"AND cd_processo NOT IN ({literals})"

    if not _silver_readable():
        sql = f"""
            WITH pending_cds AS (
                SELECT DISTINCT cd_processo
                FROM delta_scan('{bronze_lit}')
                WHERE cd_processo IS NOT NULL
                {exclude_filter}
                LIMIT {int(limit)}
            )
            SELECT * EXCLUDE (_rn) FROM (
                SELECT b.*,
                       row_number() OVER (PARTITION BY b.cd_processo ORDER BY b.cd_processo) AS _rn
                FROM delta_scan('{bronze_lit}') AS b
                INNER JOIN pending_cds p ON b.cd_processo = p.cd_processo
            ) t
            WHERE _rn = 1
        """
    else:
        silver_lit = _escape_sql_literal(str(SILVER_MOV_TABLE))
        sql = f"""
            WITH pending_cds AS (
                SELECT DISTINCT cd_processo
                FROM delta_scan('{bronze_lit}')
                WHERE cd_processo IS NOT NULL
                {exclude_filter}
                EXCEPT
                SELECT DISTINCT cd_processo
                FROM delta_scan('{silver_lit}')
                WHERE cd_processo IS NOT NULL
                LIMIT {int(limit)}
            )
            SELECT * EXCLUDE (_rn) FROM (
                SELECT b.*,
                       row_number() OVER (PARTITION BY b.cd_processo ORDER BY b.cd_processo) AS _rn
                FROM delta_scan('{bronze_lit}') AS b
                INNER JOIN pending_cds p ON b.cd_processo = p.cd_processo
            ) t
            WHERE _rn = 1
        """

    con = _duckdb_connect()
    try:
        return con.execute(sql).fetchdf()
    finally:
        con.close()


def _bronze_projection() -> list[str]:
    dt = DeltaTable(str(BRONZE_FACE_TABLE))
    names = set(dt.schema().to_arrow().names)
    cols = [c for c in METADATA_COLS if c in names]
    if "movimentações" in names:
        cols.append("movimentações")
    return cols


def load_processed_cds() -> set[str]:
    """Distinct cd_processo already in silver (stream-mode resume set)."""
    if not _silver_readable():
        return set()
    silver_lit = _escape_sql_literal(str(SILVER_MOV_TABLE))
    con = _duckdb_connect()
    try:
        rows = con.execute(
            f"""
            SELECT DISTINCT cd_processo
            FROM delta_scan('{silver_lit}')
            WHERE cd_processo IS NOT NULL
            """
        ).fetchall()
        return {str(row[0]) for row in rows if row[0] is not None}
    finally:
        con.close()


def iter_bronze_batches(parent_batch_size: int):
    dt = DeltaTable(str(BRONZE_FACE_TABLE))
    dataset = dt.to_pyarrow_dataset()
    scanner = dataset.scanner(columns=_bronze_projection(), batch_size=parent_batch_size)
    for batch in scanner.to_batches():
        yield batch.to_pandas()


def safe_json_load(val):
    if pd.isna(val) or not isinstance(val, str):
        return []
    try:
        parsed = json.loads(val)
        return parsed if isinstance(parsed, list) else []
    except json.JSONDecodeError:
        return []


def create_mov_hash(row) -> str:
    raw = (
        f"{row.get('cd_processo', '')}_"
        f"{row.get('data_movimentacao', '')}_"
        f"{row.get('tipo_movimentacao', '')}"
    )
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def empty_parent_hash(cd_processo: str) -> str:
    return hashlib.md5(f"{cd_processo}__empty".encode("utf-8")).hexdigest()


def _write_chunk(df_chunk: pd.DataFrame) -> None:
    if df_chunk.empty:
        return
    if _silver_readable():
        write_deltalake(
            str(SILVER_MOV_TABLE),
            df_chunk,
            mode="append",
            schema_mode="merge",
        )
    else:
        write_deltalake(str(SILVER_MOV_TABLE), df_chunk, mode="overwrite")


def flush_to_delta(accumulator_list: list[pd.DataFrame], *, merge_chunk_size: int) -> None:
    if not accumulator_list:
        return

    df_upsert = pd.concat(accumulator_list, ignore_index=True)
    df_upsert = df_upsert.drop_duplicates(subset=["id_movimentacao"], keep="last")
    total = len(df_upsert)

    for start in range(0, total, merge_chunk_size):
        chunk = df_upsert.iloc[start : start + merge_chunk_size]
        _write_chunk(chunk)
        del chunk
        gc.collect()

    del df_upsert
    gc.collect()


def _placeholder_rows(df: pd.DataFrame, available_cols: list[str]) -> pd.DataFrame:
    placeholder = df[available_cols].drop_duplicates(subset=["cd_processo"], keep="last").copy()
    if "numero" in placeholder.columns:
        placeholder = placeholder.rename(columns={"numero": "num_processo"})
    placeholder["data_movimentacao"] = pd.NaT
    placeholder["tipo_movimentacao"] = None
    placeholder["id_movimentacao"] = placeholder["cd_processo"].map(empty_parent_hash)
    return placeholder


def _explode_movements(df: pd.DataFrame, available_cols: list[str]) -> pd.DataFrame:
    df_subset = df[available_cols + ["movimentações"]].copy()
    df_subset["mov_list"] = df_subset["movimentações"].apply(safe_json_load)
    df_exploded = df_subset.explode("mov_list").reset_index(drop=True)
    df_exploded = df_exploded.dropna(subset=["mov_list"])
    if df_exploded.empty:
        return df_exploded

    df_exploded["data_movimentacao"] = df_exploded["mov_list"].apply(
        lambda x: x.get("data") if isinstance(x, dict) else None
    )
    df_exploded["tipo_movimentacao"] = df_exploded["mov_list"].apply(
        lambda x: x.get("tipo") if isinstance(x, dict) else None
    )
    if "numero" in df_exploded.columns:
        df_exploded = df_exploded.rename(columns={"numero": "num_processo"})
    df_exploded = df_exploded.drop(columns=["movimentações", "mov_list"])
    df_exploded["id_movimentacao"] = df_exploded.apply(create_mov_hash, axis=1)
    return df_exploded


def _parents_to_frames(df: pd.DataFrame) -> tuple[list[pd.DataFrame], int]:
    available_cols = [c for c in METADATA_COLS if c in df.columns]
    frames: list[pd.DataFrame] = []
    movement_rows = 0

    if "movimentações" not in df.columns:
        frame = _placeholder_rows(df, available_cols)
        frames.append(frame)
        movement_rows += len(frame)
        return frames, movement_rows

    df_exploded = _explode_movements(df, available_cols)
    if df_exploded.empty:
        frame = _placeholder_rows(df, available_cols)
        frames.append(frame)
        movement_rows += len(frame)
    else:
        frames.append(df_exploded)
        movement_rows += len(df_exploded)
    return frames, movement_rows


def _process_batch_loop(
    *,
    wave_parents: int,
    parent_batch_size: int,
    ram_threshold: int,
    flush_every_parents: int,
    merge_chunk_size: int,
    batch_source,
) -> int:
    accumulator: list[pd.DataFrame] = []
    accumulated_rows = 0
    parents_since_flush = 0
    parents_this_run = 0

    progress_bar = tqdm(total=wave_parents, desc="Processing Parents", unit="proc")
    for df in batch_source:
        if df.empty:
            continue

        frames, movement_rows = _parents_to_frames(df)
        accumulator.extend(frames)
        accumulated_rows += movement_rows
        parents_this_run += len(df)
        parents_since_flush += len(df)
        progress_bar.update(len(df))
        del df

        should_flush = (
            accumulated_rows >= ram_threshold or parents_since_flush >= flush_every_parents
        )
        if should_flush and accumulator:
            tqdm.write(
                f"Committing {parents_since_flush:,} parent(s), "
                f"{accumulated_rows:,} movement row(s) to Delta..."
            )
            flush_to_delta(accumulator, merge_chunk_size=merge_chunk_size)
            accumulator.clear()
            accumulated_rows = 0
            parents_since_flush = 0
            gc.collect()

        if parents_this_run >= wave_parents:
            break

    progress_bar.close()

    if accumulator:
        tqdm.write("Flushing final batch to Delta...")
        flush_to_delta(accumulator, merge_chunk_size=merge_chunk_size)
        accumulator.clear()
        gc.collect()

    return parents_this_run


def process_tail(
    *,
    pending_parents: int,
    max_parents: int | None,
    parent_batch_size: int,
    ram_threshold: int,
    flush_every_parents: int,
    merge_chunk_size: int,
) -> int:
    """Process pending parents via targeted DuckDB fetch (no full bronze scan)."""
    wave_parents = pending_parents if max_parents is None else min(pending_parents, max_parents)
    print(f"Tail mode: fetching ~{wave_parents:,} pending parent(s) via DuckDB...")

    processed_wave: set[str] = set()
    fetched = 0

    def tail_batches():
        nonlocal fetched
        while fetched < wave_parents:
            batch_limit = min(parent_batch_size, wave_parents - fetched)
            df = fetch_pending_parents_batch(batch_limit, exclude_cds=processed_wave)
            if df.empty:
                return
            df["cd_processo"] = df["cd_processo"].astype("string")
            processed_wave.update(df["cd_processo"].dropna().tolist())
            fetched += len(df)
            yield df

    handled = _process_batch_loop(
        wave_parents=wave_parents,
        parent_batch_size=parent_batch_size,
        ram_threshold=ram_threshold,
        flush_every_parents=flush_every_parents,
        merge_chunk_size=merge_chunk_size,
        batch_source=tail_batches(),
    )
    _sync_stats_after_publish(published_parents=handled)
    return handled


def process_stream(
    *,
    pending_parents: int,
    max_parents: int | None,
    parent_batch_size: int,
    ram_threshold: int,
    flush_every_parents: int,
    merge_chunk_size: int,
) -> int:
    """Single streaming pass over bronze (for large backlogs)."""
    processed = load_processed_cds()
    print(f"Found {len(processed):,} parent process(es) already stored in Silver.")

    wave_parents = pending_parents if max_parents is None else min(pending_parents, max_parents)
    if max_parents is not None:
        print(f"Capping this run to {wave_parents:,} parent process(es).")

    print(f"Streaming bronze once for ~{wave_parents:,} pending parent process(es)...")

    parents_this_run = 0

    def stream_batches():
        nonlocal parents_this_run
        for df_raw in iter_bronze_batches(parent_batch_size):
            df_raw["cd_processo"] = df_raw["cd_processo"].astype("string")
            df = df_raw[~df_raw["cd_processo"].isin(processed)]
            df = df.drop_duplicates(subset=["cd_processo"], keep="last")
            del df_raw

            if df.empty:
                continue

            if max_parents is not None:
                remaining = wave_parents - parents_this_run
                if remaining <= 0:
                    return
                if len(df) > remaining:
                    df = df.head(remaining)

            processed.update(df["cd_processo"].dropna().tolist())
            parents_this_run += len(df)
            yield df

            if max_parents is not None and parents_this_run >= wave_parents:
                return

    handled = _process_batch_loop(
        wave_parents=wave_parents,
        parent_batch_size=parent_batch_size,
        ram_threshold=ram_threshold,
        flush_every_parents=flush_every_parents,
        merge_chunk_size=merge_chunk_size,
        batch_source=stream_batches(),
    )
    _sync_stats_after_publish(published_parents=handled)
    return handled


def process_movimentacoes_to_silver(
    *,
    pending_parents: int,
    max_parents: int | None = None,
    skip_compact: bool = False,
    tail_only: bool = False,
    parent_batch_size: int = PARENT_BATCH_SIZE,
    ram_threshold: int = EXPLODED_RAM_THRESHOLD,
    flush_every_parents: int = FLUSH_EVERY_PARENTS,
    merge_chunk_size: int = MERGE_CHUNK_SIZE,
) -> int:
    if not BRONZE_FACE_TABLE.exists():
        print(f"Bronze table not found at {BRONZE_FACE_TABLE}")
        return -1

    if pending_parents == 0:
        print("All processes are already stored in Silver. Nothing to do!")
        return 0

    use_tail = tail_only or pending_parents <= TAIL_THRESHOLD
    if use_tail:
        handled = process_tail(
            pending_parents=pending_parents,
            max_parents=max_parents,
            parent_batch_size=parent_batch_size,
            ram_threshold=ram_threshold,
            flush_every_parents=flush_every_parents,
            merge_chunk_size=merge_chunk_size,
        )
    else:
        handled = process_stream(
            pending_parents=pending_parents,
            max_parents=max_parents,
            parent_batch_size=parent_batch_size,
            ram_threshold=ram_threshold,
            flush_every_parents=flush_every_parents,
            merge_chunk_size=merge_chunk_size,
        )

    if not skip_compact and _silver_readable():
        print("Running final Delta compaction for read optimization...")
        DeltaTable(str(SILVER_MOV_TABLE)).optimize.compact()
    elif skip_compact:
        print("Skipping inline Delta compact (run optimize_delta_lake.py separately).")

    print(
        f"\nSilver Movements Layer updated! {handled:,} parent process(es) handled. "
        f"Destino: {SILVER_MOV_TABLE}"
    )
    return handled


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Transform bronze face movimentações into silver movimentacoes_delta."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print pending parent count only (fast; uses cached stats).",
    )
    parser.add_argument(
        "--refresh-counts",
        action="store_true",
        help="Recount pending via slow DuckDB EXCEPT (accurate; updates cache).",
    )
    parser.add_argument(
        "--tail-only",
        action="store_true",
        help="Fetch only pending parents via DuckDB (for small tail; no full bronze scan).",
    )
    parser.add_argument(
        "--max-parents",
        type=int,
        default=None,
        metavar="N",
        help="Cap unique cd_processo handled in this run.",
    )
    parser.add_argument(
        "--skip-compact",
        action="store_true",
        help="Skip inline Delta compact() after publish.",
    )
    parser.add_argument(
        "--parent-batch-size",
        type=int,
        default=PARENT_BATCH_SIZE,
        metavar="N",
        help=f"Parents per batch (default: {PARENT_BATCH_SIZE}).",
    )
    parser.add_argument(
        "--ram-threshold",
        type=int,
        default=EXPLODED_RAM_THRESHOLD,
        metavar="N",
        help=f"Exploded movement rows before flush (default: {EXPLODED_RAM_THRESHOLD}).",
    )
    parser.add_argument(
        "--flush-every-parents",
        type=int,
        default=FLUSH_EVERY_PARENTS,
        metavar="N",
        help=f"Flush every N parents (default: {FLUSH_EVERY_PARENTS}).",
    )
    parser.add_argument(
        "--merge-chunk-size",
        type=int,
        default=MERGE_CHUNK_SIZE,
        metavar="N",
        help=f"Rows per append chunk (default: {MERGE_CHUNK_SIZE}).",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if not BRONZE_FACE_TABLE.exists():
        print(f"Bronze table not found at {BRONZE_FACE_TABLE}")
        return 1

    pending, silver_parents = count_pending_stats(refresh=args.refresh_counts)
    print(f"Bronze cd_processo pending movimentacoes: {pending:,}")
    print(f"Silver unique parents stored: {silver_parents:,}")
    print(f"pending_work={pending}")

    if args.dry_run:
        return 0
    if pending == 0:
        print("Nothing to process.")
        return 0

    processed = process_movimentacoes_to_silver(
        pending_parents=pending,
        max_parents=args.max_parents,
        skip_compact=args.skip_compact,
        tail_only=args.tail_only,
        parent_batch_size=args.parent_batch_size,
        ram_threshold=args.ram_threshold,
        flush_every_parents=args.flush_every_parents,
        merge_chunk_size=args.merge_chunk_size,
    )
    if processed < 0:
        return 1
    return 0 if processed > 0 or pending == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
