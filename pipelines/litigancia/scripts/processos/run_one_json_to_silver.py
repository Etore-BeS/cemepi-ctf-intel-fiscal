#!/usr/bin/env python3
"""Bronze + silver for a single collector JSON path. Fresh process = RAM released after exit."""

from __future__ import annotations

import argparse
import gc
import sys
from pathlib import Path

from config.scripts import load_transform_module


def main() -> int:
    parser = argparse.ArgumentParser(description="Process one collector JSON → bronze → silver.")
    parser.add_argument("json_path", type=Path, help="Path to process_grouped_all_assuntos.json")
    parser.add_argument(
        "--compact",
        action="store_true",
        help="Run Delta compact() after silver publish (off by default).",
    )
    parser.add_argument("--bronze-only", action="store_true")
    parser.add_argument("--silver-only", action="store_true")
    args = parser.parse_args()

    json_path = args.json_path.resolve()
    if not json_path.is_file():
        print(f"ERROR: not a file: {json_path}", file=sys.stderr)
        return 2

    path_str = str(json_path)
    bronze = load_transform_module("create_bronze_layer")
    silver = load_transform_module("create_silver_layer")

    if not args.silver_only:
        try:
            bronze.ingest_json_file(json_path)
        except Exception as e:
            print(f"ERROR bronze: {e}", flush=True)
            return 1
        gc.collect()

    if args.bronze_only:
        return 0

    if path_str not in silver.get_pending_bronze_paths():
        print(f"Silver: already up to date for {path_str}", flush=True)
        return 0

    try:
        processed = silver.process_bronze_to_silver(
            only_new_paths=True,
            paths={path_str},
            compact_after=args.compact,
            stage_only=False,
        )
    except Exception as e:
        print(f"ERROR silver: {e}", flush=True)
        return 1

    if not processed:
        print("ERROR silver: no rows processed", flush=True)
        return 1

    gc.collect()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
