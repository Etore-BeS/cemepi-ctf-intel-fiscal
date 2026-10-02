#!/usr/bin/env python3
"""Config-driven juscraper recoleta driver (monthly queues, per-campaign state)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from scrapers.recollect_runner import run_recollection

_SCRIPTS_DIR = Path(__file__).resolve().parents[2]
_DATA_DIR = _SCRIPTS_DIR / "data"
_DEFAULT_CONFIG = _DATA_DIR / "recollect_configs" / "round2_assunto_icms.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run juscraper recoleta from a campaign config JSON.",
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
        help="Print planned monthly folders without scraping.",
    )
    return parser.parse_args()


def main_cli() -> int:
    args = parse_args()
    config_path = args.config.resolve()
    if not config_path.is_file():
        print(f"ERROR: config not found: {config_path}", file=sys.stderr)
        return 2
    run_recollection(config_path, data_dir=_DATA_DIR, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main_cli())
