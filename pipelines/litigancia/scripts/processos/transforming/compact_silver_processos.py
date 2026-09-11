#!/usr/bin/env python3
"""One-off global dedupe on silver processos (see create_silver_layer.compact_silver_global_dedupe)."""

from __future__ import annotations

import argparse
import sys

from config.scripts import load_transform_module


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Global dedupe on silver processos_delta (monthly / post-recoleta)."
    )
    parser.add_argument(
        "--compact",
        action="store_true",
        help="Run Delta compact() after rewrite.",
    )
    parser.add_argument(
        "--test",
        action="store_true",
        help=(
            "Fast smoke test: ~1%% sample, 8 buckets, dedupe 2 buckets only, "
            "no silver rewrite (~minutes not hours)."
        ),
    )
    parser.add_argument(
        "--buckets",
        type=int,
        default=None,
        metavar="N",
        help=f"Hash buckets for dedupe (default {64}, or 8 with --test).",
    )
    parser.add_argument(
        "--max-buckets",
        type=int,
        default=None,
        metavar="N",
        help="Process only buckets 0..N-1 (partial run).",
    )
    parser.add_argument(
        "--skip-rewrite",
        action="store_true",
        help="Run shard+dedupe only; do not overwrite silver (implies safe dry run).",
    )
    parser.add_argument(
        "--sample-permille",
        type=int,
        default=None,
        metavar="P",
        help="Keep ~P/1000 of rows (e.g. 10 = 1%%) for quick tests.",
    )
    args = parser.parse_args()

    max_parquet_files = None
    if args.test:
        num_buckets = args.buckets if args.buckets is not None else 8
        max_buckets = args.max_buckets if args.max_buckets is not None else 2
        skip_rewrite = True
        sample_permille = args.sample_permille if args.sample_permille is not None else 10
        max_parquet_files = 2
    else:
        num_buckets = args.buckets
        max_buckets = args.max_buckets
        skip_rewrite = args.skip_rewrite
        sample_permille = args.sample_permille

    silver = load_transform_module("create_silver_layer")
    if num_buckets is None:
        num_buckets = silver.DEFAULT_COMPACT_BUCKETS
    silver.compact_silver_global_dedupe(
        compact_after=args.compact,
        num_buckets=num_buckets,
        max_buckets=max_buckets,
        skip_rewrite=skip_rewrite,
        sample_permille=sample_permille,
        max_parquet_files=max_parquet_files,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
