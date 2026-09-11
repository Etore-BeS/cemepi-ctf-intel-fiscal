#!/usr/bin/env python3
"""
Compact Delta Lake Parquet files across the full datalake.

Small-file fragmentation slows Polars/DuckDB scans. This script runs
Delta ``optimize.compact()`` on every production table under LAKE_ROOT,
optionally z-ordering known analytical keys and vacuuming orphaned files.

Designed for overnight runs on external USB + limited RAM (defaults:
max_concurrent_tasks=2, target file size 256 MiB).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from deltalake import DeltaTable

from config.paths import LAKE_ROOT

SKIP_DIR_NAMES = {".tmp_staging", "__pycache__"}
SKIP_PATH_FRAGMENTS = ("/.tmp_", "/compact_", "/run_")

KNOWN_Z_ORDER: dict[str, list[str]] = {
    "processos_delta": ["id_processo", "decisao"],
    "face_processos_clean_delta": ["cd_processo"],
    "face_processos_delta": ["cd_processo"],
    "movimentacoes_delta": ["id_movimentacao"],
    "coletas_delta": ["file_path"],
}

TEST_TABLE_NAME = "coletas_delta"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_skipped_table(path: Path, *, include_backups: bool) -> bool:
    rel = path.as_posix()
    name = path.name
    if name.startswith("."):
        return True
    if any(part in SKIP_DIR_NAMES for part in path.parts):
        return True
    if any(fragment in rel for fragment in SKIP_PATH_FRAGMENTS):
        return True
    if not include_backups and "back_up" in name:
        return True
    return False


def discover_delta_tables(
    lake_root: Path,
    *,
    include_backups: bool = False,
) -> list[Path]:
    if not lake_root.exists():
        raise FileNotFoundError(f"LAKE_ROOT does not exist: {lake_root}")

    tables: list[Path] = []
    for delta_log in sorted(lake_root.rglob("_delta_log")):
        if not delta_log.is_dir():
            continue
        table_path = delta_log.parent
        if _is_skipped_table(table_path, include_backups=include_backups):
            continue
        if DeltaTable.is_deltatable(str(table_path)):
            tables.append(table_path)
    return tables


def _parquet_paths(table_path: Path) -> list[Path]:
    return sorted(
        p
        for p in table_path.rglob("*.parquet")
        if "_delta_log" not in p.parts
    )


def _uri_to_path(uri: str) -> Path:
    if uri.startswith("file://"):
        return Path(uri.removeprefix("file://"))
    return Path(uri)


def table_stats(table_path: Path) -> dict:
    dt = DeltaTable(str(table_path))
    active_paths = [_uri_to_path(u) for u in dt.file_uris()]
    active_bytes = sum(p.stat().st_size for p in active_paths if p.exists())

    on_disk_parquet = _parquet_paths(table_path)
    on_disk_bytes = sum(p.stat().st_size for p in on_disk_parquet)

    return {
        "path": str(table_path),
        "relative_path": str(table_path.relative_to(LAKE_ROOT)),
        "active_parquet_files": len(active_paths),
        "on_disk_parquet_files": len(on_disk_parquet),
        "orphan_parquet_files": max(0, len(on_disk_parquet) - len(active_paths)),
        "active_bytes": active_bytes,
        "active_gib": round(active_bytes / (1024**3), 3),
        "on_disk_gib": round(on_disk_bytes / (1024**3), 3),
    }


def _schema_column_names(table_path: Path) -> set[str]:
    dt = DeltaTable(str(table_path))
    return {field.name for field in dt.schema().fields}


def _z_order_columns(table_path: Path) -> list[str] | None:
    defaults = KNOWN_Z_ORDER.get(table_path.name)
    if not defaults:
        return None
    available = _schema_column_names(table_path)
    cols = [c for c in defaults if c in available]
    return cols or None


def _compact_once(
    dt: DeltaTable,
    *,
    target_size: int,
    max_concurrent_tasks: int,
    min_commit_minutes: int,
) -> dict:
    return dt.optimize.compact(
        target_size=target_size,
        max_concurrent_tasks=max_concurrent_tasks,
        min_commit_interval=timedelta(minutes=min_commit_minutes),
    )


def optimize_table(
    table_path: Path,
    *,
    dry_run: bool,
    z_order: bool,
    target_size: int,
    max_concurrent_tasks: int,
    max_passes: int,
    min_commit_minutes: int,
    vacuum: bool,
    vacuum_retention_hours: int,
) -> dict:
    rel = str(table_path.relative_to(LAKE_ROOT))
    before = table_stats(table_path)
    result: dict = {
        "table": rel,
        "before": before,
        "passes": [],
        "z_order": None,
        "vacuum": None,
        "after": before,
        "dry_run": dry_run,
    }

    if dry_run:
        z_cols = _z_order_columns(table_path) if z_order else None
        result["planned_z_order"] = z_cols
        result["planned_vacuum"] = vacuum
        return result

    dt = DeltaTable(str(table_path))
    print(f"\n=== Optimizing {rel} ===", flush=True)
    print(
        f"  before: {before['active_parquet_files']:,} active parquet files "
        f"({before['active_gib']:.2f} GiB active; "
        f"{before['orphan_parquet_files']:,} orphans on disk)",
        flush=True,
    )

    for pass_num in range(1, max_passes + 1):
        started = time.monotonic()
        metrics = _compact_once(
            dt,
            target_size=target_size,
            max_concurrent_tasks=max_concurrent_tasks,
            min_commit_minutes=min_commit_minutes,
        )
        elapsed = time.monotonic() - started
        removed = int(metrics.get("numFilesRemoved", 0))
        added = int(metrics.get("numFilesAdded", 0))
        pass_info = {
            "pass": pass_num,
            "elapsed_sec": round(elapsed, 1),
            "metrics": metrics,
        }
        result["passes"].append(pass_info)
        print(
            f"  pass {pass_num}: removed {removed:,} files, added {added:,} "
            f"({elapsed:.1f}s)",
            flush=True,
        )
        if removed == 0:
            break
        dt = DeltaTable(str(table_path))

    if z_order:
        z_cols = _z_order_columns(table_path)
        if z_cols:
            print(f"  z-order on {z_cols}...", flush=True)
            started = time.monotonic()
            z_metrics = dt.optimize.z_order(
                z_cols,
                target_size=target_size,
                max_concurrent_tasks=max_concurrent_tasks,
                min_commit_interval=timedelta(minutes=min_commit_minutes),
            )
            elapsed = time.monotonic() - started
            result["z_order"] = {
                "columns": z_cols,
                "elapsed_sec": round(elapsed, 1),
                "metrics": z_metrics,
            }
            print(f"  z-order done ({elapsed:.1f}s)", flush=True)
            dt = DeltaTable(str(table_path))
        else:
            print("  z-order skipped (no known columns in schema)", flush=True)

    if vacuum:
        print(
            f"  vacuum (retention={vacuum_retention_hours}h, dry_run=False)...",
            flush=True,
        )
        started = time.monotonic()
        deleted = dt.vacuum(
            retention_hours=vacuum_retention_hours,
            dry_run=False,
            enforce_retention_duration=False,
        )
        elapsed = time.monotonic() - started
        result["vacuum"] = {
            "retention_hours": vacuum_retention_hours,
            "deleted_files": len(deleted),
            "elapsed_sec": round(elapsed, 1),
        }
        print(f"  vacuum removed {len(deleted):,} orphan files ({elapsed:.1f}s)", flush=True)

    after = table_stats(table_path)
    result["after"] = after
    reduced = before["active_parquet_files"] - after["active_parquet_files"]
    print(
        f"  after: {after['active_parquet_files']:,} active parquet files "
        f"({after['active_gib']:.2f} GiB active; "
        f"{after['orphan_parquet_files']:,} orphans on disk); "
        f"delta active files {reduced:+,}",
        flush=True,
    )
    return result


def _select_tables(
    all_tables: list[Path],
    *,
    only: list[str] | None,
    test: bool,
) -> list[Path]:
    if only:
        selected: list[Path] = []
        for spec in only:
            candidate = Path(spec)
            if not candidate.is_absolute():
                candidate = LAKE_ROOT / spec
            if not DeltaTable.is_deltatable(str(candidate)):
                raise ValueError(f"Not a Delta table: {candidate}")
            selected.append(candidate)
        return selected

    if test:
        matches = [t for t in all_tables if t.name == TEST_TABLE_NAME]
        if not matches:
            raise RuntimeError(
                f"--test expects table named {TEST_TABLE_NAME!r} under LAKE_ROOT"
            )
        return matches

    return sorted(all_tables, key=lambda p: table_stats(p)["active_parquet_files"], reverse=True)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compact Delta Parquet files across the full datalake."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report fragmentation only; do not rewrite files.",
    )
    parser.add_argument(
        "--test",
        action="store_true",
        help=f"Optimize only {TEST_TABLE_NAME} (smoke test before overnight run).",
    )
    parser.add_argument(
        "--table",
        action="append",
        dest="only",
        metavar="PATH",
        help="Optimize one table (absolute or relative to LAKE_ROOT). Repeatable.",
    )
    parser.add_argument(
        "--include-backups",
        action="store_true",
        help="Include back_up_* tables.",
    )
    parser.add_argument(
        "--z-order",
        action="store_true",
        help="Run z-order after compact when columns are known for the table.",
    )
    parser.add_argument(
        "--vacuum",
        action="store_true",
        help="Remove orphaned Parquet files after compact (uses retention hours).",
    )
    parser.add_argument(
        "--vacuum-retention-hours",
        type=int,
        default=0,
        metavar="H",
        help=(
            "Vacuum retention threshold in hours (default: 0 = delete unreferenced "
            "files immediately; safe when no concurrent writers)."
        ),
    )
    parser.add_argument(
        "--target-size-mb",
        type=int,
        default=256,
        metavar="MB",
        help="Approximate target Parquet file size (default: 256).",
    )
    parser.add_argument(
        "--max-concurrent-tasks",
        type=int,
        default=2,
        metavar="N",
        help="Delta compact worker count (default: 2 for low-RAM/USB).",
    )
    parser.add_argument(
        "--max-passes",
        type=int,
        default=8,
        metavar="N",
        help="Max compact passes per table until no files removed (default: 8).",
    )
    parser.add_argument(
        "--min-commit-minutes",
        type=int,
        default=10,
        metavar="M",
        help="Minimum minutes between Delta commits during long compacts.",
    )
    parser.add_argument(
        "--json-out",
        type=Path,
        default=None,
        help="Write machine-readable report (default: logs/optimize-delta-*.json).",
    )
    return parser


def main() -> int:
    args = build_arg_parser().parse_args()
    target_size = args.target_size_mb * 1024 * 1024

    all_tables = discover_delta_tables(LAKE_ROOT, include_backups=args.include_backups)
    if not all_tables:
        print(f"No Delta tables found under {LAKE_ROOT}", file=sys.stderr)
        return 1

    tables = _select_tables(all_tables, only=args.only, test=args.test)

    print(f"LAKE_ROOT: {LAKE_ROOT}", flush=True)
    print(f"Tables to process: {len(tables)}", flush=True)
    for table_path in tables:
        stats = table_stats(table_path)
        print(
            f"  - {stats['relative_path']}: "
            f"{stats['active_parquet_files']:,} active files "
            f"({stats['active_gib']:.2f} GiB)"
            + (
                f", {stats['orphan_parquet_files']:,} orphans on disk"
                if stats["orphan_parquet_files"]
                else ""
            ),
            flush=True,
        )

    if args.dry_run:
        print("\nDry run — no files rewritten.", flush=True)

    started_at = _utc_now()
    results = [
        optimize_table(
            table_path,
            dry_run=args.dry_run,
            z_order=args.z_order,
            target_size=target_size,
            max_concurrent_tasks=args.max_concurrent_tasks,
            max_passes=args.max_passes,
            min_commit_minutes=args.min_commit_minutes,
            vacuum=args.vacuum and not args.dry_run,
            vacuum_retention_hours=args.vacuum_retention_hours,
        )
        for table_path in tables
    ]

    report = {
        "generated_at": started_at,
        "finished_at": _utc_now(),
        "lake_root": str(LAKE_ROOT),
        "dry_run": args.dry_run,
        "test": args.test,
        "options": {
            "z_order": args.z_order,
            "vacuum": args.vacuum,
            "target_size_mb": args.target_size_mb,
            "max_concurrent_tasks": args.max_concurrent_tasks,
            "max_passes": args.max_passes,
        },
        "tables": results,
    }

    json_out = args.json_out
    if json_out is None and not args.dry_run:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        json_out = Path("logs") / f"optimize-delta-{stamp}.json"
    if json_out is not None:
        json_out.parent.mkdir(parents=True, exist_ok=True)
        with open(json_out, "w", encoding="utf-8") as handle:
            json.dump(report, handle, indent=2, default=str)
        print(f"\nWrote report to {json_out}", flush=True)

    if not args.dry_run:
        total_before = sum(r["before"]["active_parquet_files"] for r in results)
        total_after = sum(r["after"]["active_parquet_files"] for r in results)
        orphans = sum(r["after"]["orphan_parquet_files"] for r in results)
        print(
            f"\nDone. Active parquet files: {total_before:,} → {total_after:,} "
            f"({total_after - total_before:+,})",
            flush=True,
        )
        if orphans:
            print(
                f"Orphan files still on disk: {orphans:,}. "
                "Re-run with --vacuum to reclaim space.",
                flush=True,
            )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
