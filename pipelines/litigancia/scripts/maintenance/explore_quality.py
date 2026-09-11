"""CLI entrypoint for quality exploration."""

from __future__ import annotations

import argparse

from utils.quality_exploration import run_exploration


def main() -> None:
    parser = argparse.ArgumentParser(description="Explore PF/CNPJ quality and promotion gates")
    parser.add_argument("--per-stratum", type=int, default=15)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    run_exploration(per_stratum=args.per_stratum, seed=args.seed)


if __name__ == "__main__":
    main()
