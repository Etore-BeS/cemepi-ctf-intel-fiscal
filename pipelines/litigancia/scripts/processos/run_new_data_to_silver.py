#!/usr/bin/env python3
"""
Incremental pipeline: collector JSON on disk → bronze → silver (processos only).

Default (--per-file): each JSON in a **subprocess** (bronze→silver→exit) so macOS
reclaims RAM between months. Use --in-process only for debugging.

Legacy (--batch): ingest all JSONs to bronze, then merge all pending paths to silver.
"""

from __future__ import annotations

import argparse
import gc
import subprocess
import sys
from pathlib import Path

from config.paths import PIPELINE_ROOT
from config.scripts import (
    CREATE_SILVER_LAYER,
    RUN_ONE_JSON_TO_SILVER,
    format_command,
    load_transform_module,
)


def _preflight_silver(silver) -> bool:
    if silver.load_merged_hashes():
        return True
    if silver.silver_table_row_count() == 0:
        return True

    pending = silver.get_pending_bronze_paths()
    all_paths = silver.get_bronze_json_path_meta()
    print(
        "\nERROR: Silver table has data but no merged_hashes checkpoint.\n"
        f"  Bronze JSON paths: {len(all_paths)}, pending by hash: {len(pending)}\n"
        f"  Seed checkpoint (adjust date to leave recoleta out), then re-run:\n"
        f"    {format_command(CREATE_SILVER_LAYER, '--clear-checkpoint')}\n"
        f"    {format_command(CREATE_SILVER_LAYER, '--bootstrap-checkpoint', '--exclude-ingested-on-or-after', 'YYYY-MM-DD')}\n"
    )
    return False


def _work_queue(
    pending_json: list[Path],
    silver_pending: set[str],
) -> list[tuple[str, Path | None]]:
    disk_by_path = {str(p): p for p in pending_json}
    all_paths = sorted(set(disk_by_path) | silver_pending)

    def sort_key(path_str: str) -> tuple[int, float, str]:
        p = disk_by_path.get(path_str)
        if p is not None and p.is_file():
            return (0, p.stat().st_size, path_str)
        return (1, 0.0, path_str)

    return [
        (path_str, disk_by_path.get(path_str))
        for path_str in sorted(all_paths, key=sort_key)
    ]


def _label_for(path_str: str) -> str:
    for part in Path(path_str).parts:
        if part.startswith("coleta_fazenda_") or part.startswith("coleta_assunto_"):
            return part
    return Path(path_str).name


def _run_one_subprocess(
    json_path: Path,
    *,
    bronze_only: bool = False,
    silver_only: bool = False,
    compact: bool = False,
) -> int:
    cmd = [
        sys.executable,
        str(RUN_ONE_JSON_TO_SILVER),
        str(json_path),
    ]
    if bronze_only:
        cmd.append("--bronze-only")
    if silver_only:
        cmd.append("--silver-only")
    if compact:
        cmd.append("--compact")
    print(f"  subprocess: {' '.join(cmd[1:])}", flush=True)
    result = subprocess.run(cmd, cwd=str(PIPELINE_ROOT))
    return result.returncode


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Ingest new/changed collector JSON into bronze, then merge to silver (incremental).",
    )
    parser.add_argument(
        "--batch",
        action="store_true",
        help="Bronze for all pending JSONs, then silver for all paths (high RAM).",
    )
    parser.add_argument(
        "--in-process",
        action="store_true",
        help="Per-file mode in one process (debug). Default: subprocess per JSON.",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--bronze-only", action="store_true")
    parser.add_argument("--silver-only", action="store_true")
    parser.add_argument("--skip-sqlite", action="store_true")
    parser.add_argument("--compact", action="store_true")
    parser.add_argument(
        "--max-items",
        type=int,
        default=None,
        metavar="N",
        help="Cap silver-only phase to N paths per run (M1 overnight).",
    )
    return parser.parse_args()


def _run_batch(args: argparse.Namespace, bronze, silver) -> int:
    if not args.silver_only:
        stats = bronze.run_bronze_pipeline(ingest_sqlite_db=not args.skip_sqlite)
        if stats["json_files"] == 0 and stats["sqlite_tables"] == 0:
            print("\nBronze: nothing new to ingest.")
        else:
            print(
                f"\nBronze ingested: {stats['json_files']} JSON file(s), "
                f"{stats['sqlite_tables']} DB table(s)."
            )

    if args.bronze_only:
        return 0

    if not _preflight_silver(silver):
        return 1

    silver_pending = silver.get_pending_bronze_paths()
    if not silver_pending:
        print("Silver: no paths need merge (checkpoint hashes match bronze).")
        return 0

    print(f"\n--- Silver: merging {len(silver_pending)} bronze JSON path(s) ---")
    silver.show_pending_paths()

    processed = silver.process_bronze_to_silver(
        only_new_paths=True,
        compact_after=args.compact,
    )
    if not processed:
        return 1

    remaining = silver.get_pending_bronze_paths()
    print(f"\n=== Done. Silver paths still pending: {len(remaining)} ===")
    return 1 if remaining else 0


def _run_per_file(
    args: argparse.Namespace,
    bronze,
    silver,
    *,
    pending_json: list[Path],
    silver_pending: set[str],
) -> int:
    if not args.bronze_only and not _preflight_silver(silver):
        return 1

    queue = _work_queue(pending_json, silver_pending)
    if not queue:
        print("Nothing to do: all collector JSONs match bronze and silver checkpoint.")
        return 0

    print(f"Per-file mode: {len(queue)} path(s) to process.")
    if not args.in_process:
        print("Each JSON runs in a fresh subprocess (RAM released between files).")
    print(
        "Execution strategy: phase 1 bronze-only (small→large), phase 2 silver-only.\n"
    )

    if args.dry_run:
        for i, (path_str, disk_path) in enumerate(queue, 1):
            flags = []
            if disk_path is not None:
                flags.append("bronze")
            if path_str in silver_pending:
                flags.append("silver")
            mb = disk_path.stat().st_size / (1024 * 1024) if disk_path else 0
            print(
                f"  [{i}/{len(queue)}] {'+'.join(flags)} {mb:6.1f} MiB  {_label_for(path_str)}"
            )
        return 0

    failures = 0
    bronze_phase = [(p, disk) for p, disk in queue if disk is not None]
    silver_phase = [p for p, _ in queue if p in silver_pending]

    if not args.silver_only and bronze_phase:
        print(
            f"\n--- Phase 1/2 Bronze-only: {len(bronze_phase)} path(s) ---", flush=True
        )
        bronze_hashes = bronze.get_bronze_json_hashes() if args.in_process else None
        for i, (path_str, disk_path) in enumerate(bronze_phase, 1):
            label = _label_for(path_str)
            print(
                f"\n{'=' * 60}\n[B{i}/{len(bronze_phase)}] {label}\n{'=' * 60}",
                flush=True,
            )
            if not args.in_process:
                code = _run_one_subprocess(
                    disk_path,
                    bronze_only=True,
                    compact=args.compact,
                )
                if code != 0:
                    failures += 1
                continue
            try:
                bronze.ingest_json_file(disk_path, bronze_hashes=bronze_hashes)
            except Exception as e:
                print(f"ERROR bronze {path_str}: {e}", flush=True)
                failures += 1
            gc.collect()

    if args.bronze_only:
        print(f"\n=== Bronze-only finished. Failures: {failures} ===")
        return 1 if failures else 0

    silver_now = sorted(silver.get_pending_bronze_paths())
    if args.silver_only:
        silver_phase = silver_now
    else:
        silver_phase = [
            p
            for p in silver_now
            if p in {bp for bp, _ in bronze_phase} or p in silver_phase
        ]
        if not silver_phase:
            silver_phase = silver_now

    if args.max_items is not None and args.max_items > 0:
        silver_phase = silver_phase[: args.max_items]
        print(
            f"Silver capped to {len(silver_phase)} path(s) (--max-items).", flush=True
        )

    if silver_phase:
        print(
            f"\n--- Phase 2/2 Silver-only: {len(silver_phase)} path(s) ---", flush=True
        )
        for i, path_str in enumerate(silver_phase, 1):
            label = _label_for(path_str)
            print(
                f"\n{'=' * 60}\n[S{i}/{len(silver_phase)}] {label}\n{'=' * 60}",
                flush=True,
            )
            path_obj = Path(path_str)
            if not path_obj.is_file():
                print(f"ERROR: no on-disk JSON for {path_str}", flush=True)
                failures += 1
                continue
            if not args.in_process:
                code = _run_one_subprocess(
                    path_obj,
                    silver_only=True,
                    compact=args.compact,
                )
                if code != 0:
                    failures += 1
                continue
            try:
                processed = silver.process_bronze_to_silver(
                    only_new_paths=True,
                    paths={path_str},
                    compact_after=args.compact,
                )
                if not processed:
                    failures += 1
            except Exception as e:
                print(f"ERROR silver {path_str}: {e}", flush=True)
                failures += 1
            gc.collect()

    if not args.skip_sqlite and not args.silver_only and not args.bronze_only:
        print("\n--- Optional SQLite bronze ingest ---")
        bronze.run_bronze_pipeline(ingest_sqlite_db=True)

    remaining = len(silver.get_pending_bronze_paths())
    print(f"\n=== Per-file run finished. Failures: {failures} ===")
    print(f"  Silver paths still pending: {remaining}")
    if failures:
        return 1
    # --max-items caps work per run; leftover pending is expected (exit 0 for overnight loops).
    if remaining and args.max_items is None:
        return 1
    return 0


def main() -> int:
    args = parse_args()
    if args.bronze_only and args.silver_only:
        print("Use at most one of --bronze-only and --silver-only.")
        return 2

    bronze = load_transform_module("create_bronze_layer")
    silver = load_transform_module("create_silver_layer")

    print("=== New data → silver (incremental) ===\n")
    if args.batch:
        print("Mode: batch (all bronze, then all silver)")
    elif args.in_process:
        print("Mode: per-file in-process (bronze→silver each JSON)")
    else:
        print("Mode: per-file subprocess (bronze→silver each JSON, fresh RAM)")

    print("Scanning collector JSONs vs bronze...", flush=True)
    bronze_hashes = bronze.get_bronze_json_hashes()
    pending_json = bronze.find_pending_json_files(bronze_hashes, show_progress=True)
    silver_pending = silver.get_pending_bronze_paths()
    print(f"Collector JSON new/changed vs bronze: {len(pending_json)}")
    print(f"Silver merge pending (hash vs checkpoint): {len(silver_pending)}")

    if args.batch:
        if args.dry_run:
            for path in sorted(pending_json)[:15]:
                print(f"  - {path}")
            if len(pending_json) > 15:
                print(f"  ... and {len(pending_json) - 15} more")
            if silver_pending:
                silver.show_pending_paths()
            return 0
        return _run_batch(args, bronze, silver)

    return _run_per_file(
        args,
        bronze,
        silver,
        pending_json=pending_json,
        silver_pending=silver_pending,
    )


if __name__ == "__main__":
    raise SystemExit(main())
