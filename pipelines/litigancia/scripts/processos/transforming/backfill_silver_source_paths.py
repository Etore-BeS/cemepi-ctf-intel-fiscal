#!/usr/bin/env python3
"""Heuristic backfill of source_bronze_path on legacy silver rows (coleta / drive-download)."""

from __future__ import annotations

import argparse
import calendar
import re
import sys
from datetime import date
from pathlib import Path

import duckdb
from deltalake import DeltaTable

from config.scripts import load_transform_module

COLETA_RE = re.compile(
    r"coleta_(?:fazenda|assunto_[a-z0-9_]+)_(\d{2})_(\d{2})_(\d{4})_(\d{2})_(\d{2})_(\d{4})"
)
SQLITE_ITEMS_TABLE = "processos_comunicacoes_consolidado"


def _iso_from_folder_parts(day: str, month: str, year: str) -> str:
    """Build YYYY-MM-DD; clamp day to last valid day of month (folders may say 31/06)."""
    y, m, d = int(year), int(month), int(day)
    last_day = calendar.monthrange(y, m)[1]
    return date(y, m, min(max(d, 1), last_day)).isoformat()


def parse_coleta_range(path_str: str) -> tuple[str, str] | None:
    """Inclusive ISO date range from coleta_*_DD_MM_YYYY_DD_MM_YYYY folder name."""
    for part in Path(path_str).parts:
        match = COLETA_RE.match(part)
        if match:
            d1, m1, y1, d2, m2, y2 = match.groups()
            start = _iso_from_folder_parts(d1, m1, y1)
            end = _iso_from_folder_parts(d2, m2, y2)
            return start, end
    return None


def _range_span_days(start: str, end: str) -> int:
    return (date.fromisoformat(end) - date.fromisoformat(start)).days


def parse_sibling_db_date_range(path_str: str) -> tuple[str, str] | None:
    """
    Inclusive ISO range from min/max data_disponibilizacao in sibling .db.

    Used for drive-download paths without coleta_fazenda_* (folder YYYYMMDD is scrape
    batch date, not publication range). Requires DuckDB strptime — SQLite MIN/MAX on
    DD/MM/YYYY text is wrong.
    """
    db_path = Path(path_str).with_suffix(".db")
    if not db_path.is_file():
        return None
    db_lit = _escape_sql_literal(str(db_path))
    table_lit = _escape_sql_literal(SQLITE_ITEMS_TABLE)
    con = duckdb.connect()
    try:
        row = con.execute(
            f"""
            SELECT
                min(try_cast(strptime(data_disponibilizacao, '%d/%m/%Y') AS DATE)),
                max(try_cast(strptime(data_disponibilizacao, '%d/%m/%Y') AS DATE))
            FROM sqlite_scan('{db_lit}', '{table_lit}')
            WHERE data_disponibilizacao IS NOT NULL
            """
        ).fetchone()
    finally:
        con.close()
    if not row or row[0] is None or row[1] is None:
        return None
    start = row[0].isoformat() if hasattr(row[0], "isoformat") else str(row[0])
    end = row[1].isoformat() if hasattr(row[1], "isoformat") else str(row[1])
    return start, end


def resolve_date_range(
    path_str: str,
    *,
    include_drive_download: bool,
) -> tuple[str, str] | None:
    coleta = parse_coleta_range(path_str)
    if coleta is not None:
        return coleta
    if include_drive_download and "drive-download" in path_str:
        return parse_sibling_db_date_range(path_str)
    return None


def _escape_sql_literal(value: str) -> str:
    return value.replace("'", "''")


def _date_predicate_delta(start: str, end: str) -> str:
    """DataFusion SQL for DeltaTable.update (no strptime — use to_date)."""
    return (
        "source_bronze_path IS NULL AND "
        "to_date(data_disponibilizacao, '%d/%m/%Y') IS NOT NULL AND "
        f"to_date(data_disponibilizacao, '%d/%m/%Y') >= DATE '{start}' AND "
        f"to_date(data_disponibilizacao, '%d/%m/%Y') <= DATE '{end}'"
    )


def _date_predicate_duckdb(start: str, end: str) -> str:
    """DuckDB SQL for delta_scan counts."""
    return (
        "source_bronze_path IS NULL AND "
        "try_cast(strptime(data_disponibilizacao, '%d/%m/%Y') AS DATE) IS NOT NULL AND "
        f"try_cast(strptime(data_disponibilizacao, '%d/%m/%Y') AS DATE) >= DATE '{start}' AND "
        f"try_cast(strptime(data_disponibilizacao, '%d/%m/%Y') AS DATE) <= DATE '{end}'"
    )


def _count_matching(silver_path: Path, predicate: str) -> int:
    path_lit = _escape_sql_literal(str(silver_path))
    con = duckdb.connect()
    try:
        row = con.execute(
            f"SELECT COUNT(*) FROM delta_scan('{path_lit}') WHERE {predicate}"
        ).fetchone()
        return int(row[0]) if row else 0
    finally:
        con.close()


def _prepare_paths(
    paths: list[str],
    *,
    include_drive_download: bool = False,
    drive_download_only: bool = False,
) -> list[str]:
    """Narrowest date range first (month before multi-month / wide drive-download dumps)."""
    if drive_download_only:
        paths = [
            p for p in paths if "drive-download" in p and "coleta_fazenda_" not in p
        ]
        include_drive_download = True

    keyed: list[tuple[int, str]] = []
    for path_str in paths:
        date_range = resolve_date_range(
            path_str, include_drive_download=include_drive_download
        )
        if date_range is None:
            continue
        start, end = date_range
        keyed.append((_range_span_days(start, end), path_str))
    return [p for _, p in sorted(keyed, key=lambda x: (x[0], x[1]))]


def backfill_paths(
    silver,
    *,
    paths: list[str],
    dry_run: bool = False,
    include_drive_download: bool = False,
    drive_download_only: bool = False,
) -> int:
    silver.ensure_silver_source_column()
    if not silver.SILVER_TABLE_PATH.exists() or not DeltaTable.is_deltatable(
        str(silver.SILVER_TABLE_PATH)
    ):
        print("Silver table not found.", file=sys.stderr)
        return 1

    dt = DeltaTable(str(silver.SILVER_TABLE_PATH))
    total_updated = 0
    failures = 0
    ordered = _prepare_paths(
        paths,
        include_drive_download=include_drive_download,
        drive_download_only=drive_download_only,
    )
    skipped = len(paths) - len(ordered)

    if skipped:
        print(
            f"Skipping {skipped} path(s) without a resolvable date range.", flush=True
        )
    mode = "coleta folder ranges"
    if drive_download_only:
        mode = "drive-download (.db min/max)"
    elif include_drive_download:
        mode = "coleta + drive-download (.db min/max)"
    print(
        f"Backfill order: {len(ordered)} path(s) ({mode}), narrowest range first.",
        flush=True,
    )

    for path_str in ordered:
        date_range = resolve_date_range(
            path_str,
            include_drive_download=include_drive_download or drive_download_only,
        )
        assert date_range is not None
        start, end = date_range
        pred_delta = _date_predicate_delta(start, end)
        pred_duckdb = _date_predicate_duckdb(start, end)
        span = _range_span_days(start, end)

        print(f"\n{path_str}")
        print(f"  range: {start} .. {end} ({span + 1} days)", end="", flush=True)
        try:
            n_match = _count_matching(silver.SILVER_TABLE_PATH, pred_duckdb)
        except Exception as e:
            print(f"  ERROR counting matches: {e}", file=sys.stderr)
            failures += 1
            continue
        print(f"  matching NULL source rows: {n_match:,}")

        if dry_run or n_match == 0:
            continue

        try:
            metrics = dt.update(
                new_values={"source_bronze_path": path_str},
                predicate=pred_delta,
            )
        except Exception as e:
            print(f"  ERROR update: {e}", file=sys.stderr)
            failures += 1
            continue
        updated = int(metrics.get("num_updated_rows", 0))
        total_updated += updated
        print(f"  updated: {updated:,}")

    print(f"\nBackfill complete. Total rows updated: {total_updated:,}")
    if failures:
        print(f"Paths with errors: {failures}", file=sys.stderr)
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Backfill source_bronze_path on silver using coleta folder and/or "
        "drive-download .db date ranges."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print matching row counts only; do not update.",
    )
    parser.add_argument(
        "--include-drive-download",
        action="store_true",
        help="Also backfill drive-download JSON paths via sibling .db min/max dates "
        "(in addition to coleta_fazenda_* folder ranges).",
    )
    parser.add_argument(
        "--drive-download-only",
        action="store_true",
        help="Only paths under drive-download-* (uses .db range when no coleta folder).",
    )
    parser.add_argument(
        "--path",
        action="append",
        metavar="FILE_PATH",
        help="Only this collector JSON path (repeatable). Default: all bronze JSON paths.",
    )
    args = parser.parse_args()

    if args.include_drive_download and args.drive_download_only:
        print(
            "Use --drive-download-only alone, or --include-drive-download for both.",
            file=sys.stderr,
        )
        return 2

    silver = load_transform_module("create_silver_layer")
    if args.path:
        paths = list(args.path)
    else:
        paths = sorted(silver.get_json_bronze_paths_from_delta())

    if not paths:
        print("No bronze JSON paths found.")
        return 0

    return backfill_paths(
        silver,
        paths=paths,
        dry_run=args.dry_run,
        include_drive_download=args.include_drive_download,
        drive_download_only=args.drive_download_only,
    )


if __name__ == "__main__":
    raise SystemExit(main())
