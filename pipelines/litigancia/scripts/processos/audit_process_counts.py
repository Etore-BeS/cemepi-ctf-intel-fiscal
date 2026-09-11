#!/usr/bin/env python3
"""
Audit canonical process counts across COLLECT_ROOT, bronze, and silver.

Bronze coletas_delta (modern JSON path) stores one metadata row per collector
file, not one row per process. Use this script instead of COUNT(*) on bronze
when reporting collected process volume.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import polars as pl
from deltalake import DeltaTable

from config.paths import BRONZE_COLETAS, COLLECT_ROOT, SILVER_PROCESSOS
from config.scripts import load_transform_module

JSON_SUFFIX = "process_grouped_all_assuntos.json"
DB_SUFFIX = "process_grouped_all_assuntos.db"
SQLITE_ITEMS_TABLE = "processos_comunicacoes_consolidado"


def _delta_row_count(path: Path) -> int:
    if not path.exists() or not DeltaTable.is_deltatable(str(path)):
        return 0
    return DeltaTable(str(path)).to_pyarrow_dataset().count_rows()


def _bronze_json_meta() -> pl.DataFrame:
    if not BRONZE_COLETAS.exists() or not DeltaTable.is_deltatable(str(BRONZE_COLETAS)):
        return pl.DataFrame(
            schema={
                "file_path": pl.Utf8,
                "row_hash": pl.Utf8,
                "ingested_at": pl.Datetime(time_unit="us", time_zone="UTC"),
            }
        )

    df = pl.from_arrow(
        DeltaTable(str(BRONZE_COLETAS)).to_pyarrow_table(
            columns=["file_path", "row_hash", "ingested_at"]
        )
    )
    return (
        df.filter(pl.col("file_path").str.ends_with(JSON_SUFFIX))
        .sort("ingested_at")
        .group_by("file_path")
        .agg(
            pl.col("row_hash").last().alias("row_hash"),
            pl.col("ingested_at").last().alias("ingested_at"),
        )
    )


def _collect_disk_jsons() -> list[Path]:
    return sorted(COLLECT_ROOT.rglob(JSON_SUFFIX))


def _json_count(path: Path) -> int:
    with open(path, encoding="utf-8") as handle:
        payload = json.load(handle)
    return int(payload.get("count", 0))


def _db_count(path: Path) -> int:
    if not path.exists():
        return -1
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        row = conn.execute(
            f"SELECT COUNT(*) FROM {SQLITE_ITEMS_TABLE}"
        ).fetchone()
        return int(row[0]) if row else 0
    finally:
        conn.close()


def _collect_source_counts() -> dict:
    json_files = _collect_disk_jsons()
    per_file: list[dict] = []
    sum_json_count = 0
    sum_db_count = 0
    db_missing = 0
    count_mismatch = 0

    for json_path in json_files:
        json_count = _json_count(json_path)
        db_path = json_path.with_suffix(".db")
        if db_path.name != DB_SUFFIX:
            db_path = json_path.parent / DB_SUFFIX
        db_count = _db_count(db_path)

        sum_json_count += json_count
        if db_count >= 0:
            sum_db_count += db_count
            if db_count != json_count:
                count_mismatch += 1
        else:
            db_missing += 1

        per_file.append(
            {
                "file_path": str(json_path.resolve()),
                "json_count": json_count,
                "db_count": db_count,
            }
        )

    return {
        "json_files_on_disk": len(json_files),
        "sum_json_count": sum_json_count,
        "sum_db_count": sum_db_count,
        "db_missing": db_missing,
        "json_db_mismatch_files": count_mismatch,
        "per_file": per_file,
    }


def _bronze_coverage(bronze_meta: pl.DataFrame, disk_jsons: list[Path]) -> dict:
    disk_paths = {str(p.resolve()) for p in disk_jsons}
    bronze_paths = set(bronze_meta["file_path"].to_list()) if bronze_meta.height else set()

    legacy_rows = 0
    if BRONZE_COLETAS.exists() and DeltaTable.is_deltatable(str(BRONZE_COLETAS)):
        con = duckdb.connect()
        try:
            legacy_rows = con.execute(
                f"""
                SELECT COUNT(*)
                FROM delta_scan('{BRONZE_COLETAS}')
                WHERE file_path NOT LIKE '%process_grouped_all_assuntos.json'
                """
            ).fetchone()[0]
        except duckdb.Error:
            legacy_rows = -1
        finally:
            con.close()

    return {
        "bronze_physical_rows": _delta_row_count(BRONZE_COLETAS),
        "bronze_json_paths_latest": len(bronze_paths),
        "disk_json_paths": len(disk_paths),
        "on_disk_not_in_bronze": len(disk_paths - bronze_paths),
        "in_bronze_not_on_disk": len(bronze_paths - disk_paths),
        "legacy_sqlite_rows_with_raw_data": legacy_rows,
        "legacy_note": "Non-JSON file_path rows from aggregated_database.db ingest (not modern metadata-only JSON path)",
    }


def _silver_counts() -> dict:
    if not SILVER_PROCESSOS.exists() or not DeltaTable.is_deltatable(str(SILVER_PROCESSOS)):
        return {
            "silver_rows": 0,
            "unique_id_processo": 0,
            "unique_cd_processo": 0,
            "null_id_processo": 0,
        }

    con = duckdb.connect()
    try:
        row = con.execute(
            f"""
            SELECT
                COUNT(*) AS silver_rows,
                COUNT(DISTINCT id_processo) AS unique_id_processo,
                COUNT(DISTINCT cd_processo) AS unique_cd_processo,
                COUNT(*) FILTER (WHERE id_processo IS NULL) AS null_id_processo
            FROM delta_scan('{SILVER_PROCESSOS}')
            """
        ).fetchone()
    finally:
        con.close()

    return {
        "silver_rows": int(row[0]),
        "unique_id_processo": int(row[1]),
        "unique_cd_processo": int(row[2]),
        "null_id_processo": int(row[3]),
    }


def _silver_overlap() -> dict:
    if not SILVER_PROCESSOS.exists() or not DeltaTable.is_deltatable(str(SILVER_PROCESSOS)):
        return {
            "processes_in_multiple_sources": 0,
            "decision_keys_in_multiple_sources": 0,
        }

    con = duckdb.connect()
    try:
        multi_source_processes = con.execute(
            f"""
            SELECT COUNT(*) FROM (
                SELECT id_processo
                FROM delta_scan('{SILVER_PROCESSOS}')
                WHERE id_processo IS NOT NULL
                GROUP BY id_processo
                HAVING COUNT(DISTINCT source_bronze_path) > 1
            )
            """
        ).fetchone()[0]
        multi_source_decisions = con.execute(
            f"""
            SELECT COUNT(*) FROM (
                SELECT id_processo, decisao
                FROM delta_scan('{SILVER_PROCESSOS}')
                WHERE id_processo IS NOT NULL
                GROUP BY id_processo, decisao
                HAVING COUNT(DISTINCT source_bronze_path) > 1
            )
            """
        ).fetchone()[0]
    finally:
        con.close()

    return {
        "processes_in_multiple_sources": int(multi_source_processes),
        "decision_keys_in_multiple_sources": int(multi_source_decisions),
        "_note": "Overlap inflates SUM(json_count) vs silver unique id_processo",
    }


def _pending_silver_paths() -> dict:
    silver = load_transform_module("create_silver_layer")
    current = silver.get_bronze_json_path_meta()
    pending = silver.get_pending_bronze_paths()
    merged = silver.load_merged_hashes()
    return {
        "bronze_json_paths": len(current),
        "checkpoint_hashes": len(merged),
        "pending_for_silver": len(pending),
        "pending_paths": sorted(pending),
    }


def build_report(*, include_overlap: bool = True) -> dict:
    print("Scanning collector JSON files...", flush=True)
    bronze_meta = _bronze_json_meta()
    disk_jsons = _collect_disk_jsons()
    source = _collect_source_counts()
    print("Reading bronze coverage...", flush=True)
    bronze = _bronze_coverage(bronze_meta, disk_jsons)
    print("Counting silver rows and uniques (may take a few minutes)...", flush=True)
    silver = _silver_counts()
    print("Checking silver pipeline backlog...", flush=True)
    pending = _pending_silver_paths()

    overlap = {}
    if include_overlap:
        print("Scanning cross-month overlap in silver...", flush=True)
        overlap = _silver_overlap()

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "paths": {
            "collect_root": str(COLLECT_ROOT),
            "bronze_coletas": str(BRONZE_COLETAS),
            "silver_processos": str(SILVER_PROCESSOS),
        },
        "canonical_metrics": {
            "collected_processes_sum_json_count": source["sum_json_count"],
            "collected_processes_sum_db_rows": source["sum_db_count"],
            "unique_processes_silver": silver["unique_id_processo"],
            "silver_decision_rows": silver["silver_rows"],
        },
        "bronze_semantics": {
            "model": "one metadata row per collector JSON (not one row per process)",
            "do_not_use": "COUNT(*) on coletas_delta as process total (unless legacy raw_data rows exist)",
            "use_instead": [
                "SUM(count) in process_grouped_all_assuntos.json under COLLECT_ROOT",
                "SUM(COUNT(*) FROM processos_comunicacoes_consolidado in sibling .db files",
                "COUNT(DISTINCT id_processo) in silver processos_delta for global unique processes",
            ],
        },
        "source_files": source,
        "bronze": bronze,
        "silver": silver,
        "silver_overlap": overlap,
        "silver_pending": pending,
        "reconciliation": {
            "sum_json_minus_unique_silver": source["sum_json_count"] - silver["unique_id_processo"],
            "sum_json_minus_silver_rows": source["sum_json_count"] - silver["silver_rows"],
            "explanation": (
                "sum_json_count can exceed unique silver processes when monthly coletas overlap; "
                "silver_rows can exceed sum_json_count because silver grain is (id_processo, decisao)."
            ),
        },
    }
    return report


def _print_summary(report: dict) -> None:
    cm = report["canonical_metrics"]
    bronze = report["bronze"]
    pending = report["silver_pending"]

    print("=== Canonical process counts ===")
    print(f"COLLECT_ROOT: {report['paths']['collect_root']}")
    print()
    print(f"Collected processes (SUM json count):     {cm['collected_processes_sum_json_count']:,}")
    print(f"Collected processes (SUM sibling .db):  {cm['collected_processes_sum_db_rows']:,}")
    print(f"Silver decision rows (COUNT):           {cm['silver_decision_rows']:,}")
    print(f"Unique processes in silver (DISTINCT):  {cm['unique_processes_silver']:,}")
    print()
    print("=== Bronze (coletas_delta) — not process rows ===")
    print(f"Physical bronze rows:                     {bronze['bronze_physical_rows']:,}")
    print(f"Latest JSON paths in bronze:              {bronze['bronze_json_paths_latest']:,}")
    print(f"JSON files on disk:                       {bronze['disk_json_paths']:,}")
    print(f"On disk, not in bronze:                   {bronze['on_disk_not_in_bronze']:,}")
    print(f"In bronze, not on disk:                   {bronze['in_bronze_not_on_disk']:,}")
    print(f"Legacy rows with raw_data:                {bronze['legacy_sqlite_rows_with_raw_data']:,}")
    print()
    print("=== Silver pipeline backlog ===")
    print(f"Pending for silver merge:                 {pending['pending_for_silver']:,}")
    if pending["pending_for_silver"]:
        print("Pending paths:")
        for path in pending["pending_paths"][:20]:
            print(f"  - {path}")
        if pending["pending_for_silver"] > 20:
            print(f"  ... and {pending['pending_for_silver'] - 20} more")
    print()
    if report.get("silver_overlap"):
        ov = report["silver_overlap"]
        print("=== Cross-month overlap (silver) ===")
        print(f"Processes in multiple source files:       {ov['processes_in_multiple_sources']:,}")
        print(f"(id_processo, decisao) in multiple src:   {ov['decision_keys_in_multiple_sources']:,}")
        print()
    rec = report["reconciliation"]
    print("=== Reconciliation ===")
    print(f"SUM(json) - unique silver processes:      {rec['sum_json_minus_unique_silver']:,}")
    print(f"SUM(json) - silver rows:                  {rec['sum_json_minus_silver_rows']:,}")
    print(rec["explanation"])


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Audit canonical process counts (collect root, bronze, silver)."
    )
    parser.add_argument(
        "--json-out",
        type=Path,
        help="Optional path to write full JSON report.",
    )
    parser.add_argument(
        "--skip-overlap",
        action="store_true",
        help="Skip cross-month overlap scan (faster on large silver tables).",
    )
    args = parser.parse_args()

    report = build_report(include_overlap=not args.skip_overlap)
    _print_summary(report)

    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        slim = dict(report)
        slim.pop("source_files", None)
        slim["source_files"] = {
            k: v
            for k, v in report["source_files"].items()
            if k != "per_file"
        }
        slim["source_files"]["per_file_count"] = len(report["source_files"]["per_file"])
        with open(args.json_out, "w", encoding="utf-8") as handle:
            json.dump(slim, handle, indent=2, default=str)
        print(f"\nWrote report to {args.json_out}")


if __name__ == "__main__":
    main()
