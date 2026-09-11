"""Export manual-review CSVs for tipo_movimentacao and tipo_sentença normalization."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from config.paths import PIPELINE_ROOT, SILVER_FACE_CLEAN, SILVER_MOVIMENTACOES
from utils.normalization_review import (
    DEFAULT_DUCKDB_MEMORY_LIMIT,
    build_round_slice,
    coverage_stats,
    export_review_csvs,
    passes_quality_gates,
)

OUTPUT_DIR = PIPELINE_ROOT / "notebooks/playground/output"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Export frequency-ranked manual review CSVs for normalization (OOM-safe DuckDB aggregation)"
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=OUTPUT_DIR,
        help="Directory for review CSV outputs",
    )
    parser.add_argument(
        "--memory-limit",
        default=DEFAULT_DUCKDB_MEMORY_LIMIT,
        help="DuckDB memory_limit for delta_scan aggregation (default: DUCKDB_MEMORY_LIMIT env or 3GB)",
    )
    parser.add_argument(
        "--no-merge",
        action="store_true",
        help="Do not preserve manual edits from existing CSV files",
    )
    parser.add_argument(
        "--round",
        type=int,
        choices=(1, 2, 3),
        help="Also export a round-specific slice CSV (1=top frequency, 2=tail, 3=quality)",
    )
    parser.add_argument(
        "--check-quality",
        action="store_true",
        help="Evaluate quality gates on existing normalizado_final / resultado labels",
    )
    args = parser.parse_args()

    df_mov, df_sent = export_review_csvs(
        movimentacoes_path=SILVER_MOVIMENTACOES,
        face_path=SILVER_FACE_CLEAN,
        output_dir=args.output_dir,
        merge_existing=not args.no_merge,
        memory_limit=args.memory_limit,
    )

    mov_path = args.output_dir / "manual_review_tipo_movimentacao.csv"
    sent_path = args.output_dir / "manual_review_tipo_sentenca.csv"
    print(f"Movimentação review: {mov_path} ({len(df_mov):,} unique values)")
    print(f"Sentença review: {sent_path} ({len(df_sent):,} unique values)")

    mov_stats = coverage_stats(df_mov)
    sent_stats = coverage_stats(df_sent)
    stats_path = args.output_dir / "normalization_review_stats.json"
    stats_path.write_text(
        json.dumps(
            {"movimentacao": mov_stats, "sentenca": sent_stats},
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"Stats: {stats_path}")

    if args.round is not None:
        round_mov = build_round_slice(df_mov, round_number=args.round)
        round_sent = build_round_slice(df_sent, round_number=args.round)
        round_mov_path = args.output_dir / f"manual_review_tipo_movimentacao_r{args.round}.csv"
        round_sent_path = args.output_dir / f"manual_review_tipo_sentenca_r{args.round}.csv"
        round_mov.write_csv(round_mov_path)
        round_sent.write_csv(round_sent_path)
        print(f"Round {args.round} movimentação slice: {round_mov_path} ({len(round_mov):,} rows)")
        print(f"Round {args.round} sentença slice: {round_sent_path} ({len(round_sent):,} rows)")

    if args.check_quality:
        mov_ok, mov_failures = passes_quality_gates(mov_stats, is_sentenca=False)
        sent_ok, sent_failures = passes_quality_gates(sent_stats, is_sentenca=True)
        for label, ok, failures in (
            ("movimentacao", mov_ok, mov_failures),
            ("sentenca", sent_ok, sent_failures),
        ):
            if ok:
                print(f"Quality gates passed for {label}.")
            else:
                print(f"Quality gates failed for {label}:")
                for failure in failures:
                    print(f"  - {failure}")


if __name__ == "__main__":
    main()
