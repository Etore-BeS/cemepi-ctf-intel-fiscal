#!/usr/bin/env python3
"""Config-driven full recoleta pipeline: processos + FACE per month."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from scrapers.recollect_runner import run_pipeline

_SCRIPTS_DIR = Path(__file__).resolve().parents[2]
_DATA_DIR = _SCRIPTS_DIR / "data"
_DEFAULT_CONFIG = _DATA_DIR / "recollect_configs" / "round2_assunto_icms.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run full recoleta pipeline per month: "
            "CJPG scrape → processos bronze/silver → FACE scrape → face silver."
        ),
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=_DEFAULT_CONFIG,
        help=f"Campaign config path (default: {_DEFAULT_CONFIG.relative_to(_SCRIPTS_DIR.parent)})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List planned monthly phases without executing.",
    )
    parser.add_argument(
        "--scrape-only",
        action="store_true",
        help="Only run juscraper collection (no bronze/silver/face).",
    )
    parser.add_argument(
        "--ingest-only",
        action="store_true",
        help="Only bronze→silver processos for months already scraped.",
    )
    parser.add_argument(
        "--face-only",
        action="store_true",
        help="Only FACE scrape + silver for months with processos ingest done.",
    )
    parser.add_argument(
        "--skip-face",
        action="store_true",
        help="Skip FACE steps (processos pipeline only).",
    )
    parser.add_argument(
        "--compact",
        action="store_true",
        help="Run Delta compact() after silver publish.",
    )
    parser.add_argument(
        "--face-workers",
        type=int,
        default=None,
        metavar="N",
        help="Override face.workers from config (higher = faster, more rate-limit risk).",
    )
    parser.add_argument(
        "--face-delay-sec",
        type=float,
        default=None,
        metavar="SEC",
        help="Override face.delay_sec from config (lower = faster).",
    )
    return parser.parse_args()


def main_cli() -> int:
    args = parse_args()
    config_path = args.config.resolve()
    if not config_path.is_file():
        print(f"ERROR: config not found: {config_path}", file=sys.stderr)
        return 2
    try:
        run_pipeline(
            config_path,
            data_dir=_DATA_DIR,
            dry_run=args.dry_run,
            scrape_only=args.scrape_only,
            ingest_only=args.ingest_only,
            face_only=args.face_only,
            skip_face=args.skip_face,
            compact=args.compact,
            face_workers=args.face_workers,
            face_delay_sec=args.face_delay_sec,
        )
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main_cli())
