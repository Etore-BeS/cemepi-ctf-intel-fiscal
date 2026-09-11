#!/usr/bin/env python3
"""
Scrape TJSP process face pages for cd_processo in silver not yet in bronze face.

Uses DuckDB anti-join (no full silver load into RAM). Tuned for residential IP:
low parallelism + delay between requests.
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import duckdb
import pandas as pd
from deltalake import DeltaTable, write_deltalake
from tqdm import tqdm

from config.paths import BRONZE_FACE, LAKE_ROOT, SILVER_PROCESSOS
from scrapers.face_tjsp import FaceTJSPScraper

SILVER_TABLE_PATH = SILVER_PROCESSOS
BRONZE_FACE_TABLE = BRONZE_FACE
FAIL_LOG = LAKE_ROOT / "silver_layer" / "face_scrape_failures.log"

# Defaults: residential IP — slow and steady
DEFAULT_WORKERS = 2
DEFAULT_DELAY_SEC = 1.5
DEFAULT_FETCH_BATCH = 500
DEFAULT_DELTA_BATCH = 200

_thread_scraper = threading.local()


def _get_scraper(
    *,
    request_timeout: float,
    max_request_retries: int,
) -> FaceTJSPScraper:
    scraper = getattr(_thread_scraper, "scraper", None)
    if scraper is None:
        scraper = FaceTJSPScraper(
            request_timeout=request_timeout,
            max_request_retries=max_request_retries,
        )
        _thread_scraper.scraper = scraper
    return scraper


def _escape_sql_literal(value: str) -> str:
    return value.replace("'", "''")


def _bronze_face_readable() -> bool:
    if not BRONZE_FACE_TABLE.exists():
        return False
    return DeltaTable.is_deltatable(str(BRONZE_FACE_TABLE))


def _pending_predicate(source_path: str | None) -> str:
    silver_lit = _escape_sql_literal(str(SILVER_TABLE_PATH))
    source_filter = ""
    if source_path:
        path_lit = _escape_sql_literal(source_path)
        source_filter = f" AND source_bronze_path = '{path_lit}'"
    silver_sql = f"""
        SELECT DISTINCT trim(cast(cd_processo AS VARCHAR)) AS cd_processo
        FROM delta_scan('{silver_lit}')
        WHERE cd_processo IS NOT NULL
          AND trim(cast(cd_processo AS VARCHAR)) != ''
          {source_filter}
    """
    if _bronze_face_readable():
        face_lit = _escape_sql_literal(str(BRONZE_FACE_TABLE))
        return f"""
            {silver_sql}
            EXCEPT
            SELECT DISTINCT trim(cast(cd_processo AS VARCHAR)) AS cd_processo
            FROM delta_scan('{face_lit}')
            WHERE cd_processo IS NOT NULL
        """
    return silver_sql


def count_pending(*, source_path: str | None = None) -> int:
    con = duckdb.connect()
    try:
        pred = _pending_predicate(source_path)
        row = con.execute(f"SELECT count(*) FROM ({pred}) AS pending").fetchone()
        return int(row[0]) if row else 0
    finally:
        con.close()


def fetch_pending_batch(
    limit: int,
    *,
    source_path: str | None = None,
) -> list[str]:
    con = duckdb.connect()
    try:
        pred = _pending_predicate(source_path)
        rows = con.execute(
            f"""
            SELECT cd_processo FROM ({pred}) AS pending
            ORDER BY cd_processo
            LIMIT {int(limit)}
            """
        ).fetchall()
        return [str(r[0]) for r in rows if r and r[0]]
    finally:
        con.close()


def _log_failure(cd_processo: str, message: str) -> None:
    FAIL_LOG.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).isoformat()
    with FAIL_LOG.open("a", encoding="utf-8") as f:
        f.write(f"{stamp}\t{cd_processo}\t{message}\n")


def fetch_and_parse(
    cd_processo: str,
    delay_sec: float,
    *,
    request_timeout: float,
    max_request_retries: int,
) -> dict | None:
    if delay_sec > 0:
        time.sleep(delay_sec)
    scraper = _get_scraper(
        request_timeout=request_timeout,
        max_request_retries=max_request_retries,
    )
    url = f"https://esaj.tjsp.jus.br/cpopg/show.do?processo.codigo={cd_processo}"
    try:
        html = scraper.get_html(url)
        if not html:
            _log_failure(cd_processo, "empty html")
            return None
        parsed_data = scraper.parse_process(html)
        if not parsed_data:
            _log_failure(cd_processo, "parse returned empty")
            return None
        for col in ["movimentações", "tipo_sentença", "data_sentença"]:
            if col in parsed_data:
                parsed_data[col] = json.dumps(parsed_data[col], ensure_ascii=False)
        parsed_data["cd_processo"] = cd_processo
        parsed_data["url_scraped"] = url
        parsed_data["ingested_at"] = datetime.now(timezone.utc)
        return parsed_data
    except Exception as e:
        _log_failure(cd_processo, str(e))
        return None


def write_batch_to_delta(records: list[dict]) -> None:
    if not records:
        return
    df_batch = pd.DataFrame(records)
    BRONZE_FACE_TABLE.parent.mkdir(parents=True, exist_ok=True)
    if DeltaTable.is_deltatable(str(BRONZE_FACE_TABLE)):
        write_deltalake(
            str(BRONZE_FACE_TABLE), df_batch, mode="append", schema_mode="merge"
        )
    else:
        write_deltalake(str(BRONZE_FACE_TABLE), df_batch, mode="overwrite")


def scrape_batch(
    cds: list[str],
    *,
    workers: int,
    delay_sec: float,
    delta_batch: int,
    request_timeout: float,
    max_request_retries: int,
    on_item_done: Callable[[], None] | None = None,
) -> tuple[int, int]:
    """Returns (success_count, failure_count)."""
    records: list[dict] = []
    ok = 0
    fail = 0

    with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        futures = {
            executor.submit(
                fetch_and_parse,
                cd,
                delay_sec,
                request_timeout=request_timeout,
                max_request_retries=max_request_retries,
            ): cd
            for cd in cds
        }
        for future in as_completed(futures):
            cd = futures[future]
            try:
                result = future.result()
            except Exception as exc:
                _log_failure(cd, f"worker: {exc}")
                fail += 1
            else:
                if result:
                    records.append(result)
                    ok += 1
                    if len(records) >= delta_batch:
                        write_batch_to_delta(records)
                        records.clear()
                else:
                    fail += 1
            if on_item_done is not None:
                on_item_done()

    if records:
        write_batch_to_delta(records)
    return ok, fail


def run_scrape(args: argparse.Namespace) -> int:
    pending_total = count_pending(source_path=args.source_path)
    print(f"Silver cds without bronze face: {pending_total:,}")
    if args.dry_run:
        return 0
    if pending_total == 0:
        print("Nothing to scrape.")
        print("Run finished. Scraped=0 failures=0")
        return 0

    cap = args.max_cds if args.max_cds and args.max_cds > 0 else None
    if cap:
        print(f"Capping this run to {cap:,} process(es).")

    print(
        f"Workers={args.workers}, delay={args.delay_sec}s, "
        f"fetch_batch={args.fetch_batch}, delta_batch={args.delta_batch}, "
        f"timeout={args.request_timeout}s, retries={args.max_request_retries}"
    )

    queue_limit = cap if cap is not None else pending_total
    cds_queue = fetch_pending_batch(queue_limit, source_path=args.source_path)
    if not cds_queue:
        print("Nothing to scrape.")
        print("Run finished. Scraped=0 failures=0")
        return 0
    print(f"Loaded {len(cds_queue):,} cd(s) into run queue (one DuckDB query).")
    print(
        "Progress updates per processo (not per wave). "
        f"ETA ~{(len(cds_queue) / max(1, args.workers)) * args.delay_sec / 60:.0f} min "
        "delay-only lower bound."
    )

    scraped = 0
    failures = 0
    wave = 0
    queue_offset = 0

    with tqdm(total=cap or len(cds_queue), desc="Scraping TJSP", unit="proc") as pbar:
        while queue_offset < len(cds_queue):
            if cap is not None and scraped >= cap:
                break
            wave += 1
            batch_limit = args.fetch_batch
            if cap is not None:
                batch_limit = min(batch_limit, cap - scraped)
            cds = cds_queue[queue_offset : queue_offset + batch_limit]
            if not cds:
                break
            queue_offset += len(cds)

            ok, fail = scrape_batch(
                cds,
                workers=args.workers,
                delay_sec=args.delay_sec,
                delta_batch=args.delta_batch,
                request_timeout=args.request_timeout,
                max_request_retries=args.max_request_retries,
                on_item_done=pbar.update,
            )
            scraped += ok
            failures += fail
            tqdm.write(
                f"wave {wave}: ok={ok:,} fail={fail:,} "
                f"(run total ok={scraped:,} fail={failures:,})"
            )

    print(f"\nRun finished. Scraped={scraped:,} failures={failures:,}")
    if args.skip_end_count:
        remaining_est = max(0, pending_total - scraped)
        print(f"Still pending (estimate): {remaining_est:,}")
    else:
        remaining = count_pending(source_path=args.source_path)
        print(f"Still pending: {remaining:,}")

    if args.compact and DeltaTable.is_deltatable(str(BRONZE_FACE_TABLE)):
        print("Compacting bronze face Delta...")
        DeltaTable(str(BRONZE_FACE_TABLE)).optimize.compact()

    print(f"Bronze face: {BRONZE_FACE_TABLE}")
    if failures:
        print(f"Failure log: {FAIL_LOG}")
    return 0 if scraped > 0 or pending_total == 0 else 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Scrape TJSP face for silver cd_processo not in bronze face."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print pending count only.",
    )
    parser.add_argument(
        "--max-cds",
        type=int,
        default=None,
        metavar="N",
        help="Cap processos scraped in this run (for overnight batches).",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=DEFAULT_WORKERS,
        help=f"Parallel HTTP workers (default {DEFAULT_WORKERS} for residential IP).",
    )
    parser.add_argument(
        "--delay-sec",
        type=float,
        default=DEFAULT_DELAY_SEC,
        help=f"Sleep before each request per worker (default {DEFAULT_DELAY_SEC}).",
    )
    parser.add_argument(
        "--fetch-batch",
        type=int,
        default=DEFAULT_FETCH_BATCH,
        help=f"In-memory wave size per batch (default {DEFAULT_FETCH_BATCH}).",
    )
    parser.add_argument(
        "--delta-batch",
        type=int,
        default=DEFAULT_DELTA_BATCH,
        help=f"Append to Delta every N successes (default {DEFAULT_DELTA_BATCH}).",
    )
    parser.add_argument(
        "--request-timeout",
        type=float,
        default=15.0,
        help="HTTP read timeout per attempt in seconds (default 15).",
    )
    parser.add_argument(
        "--max-request-retries",
        type=int,
        default=2,
        help="Retries on transient HTTP errors per processo (default 2).",
    )
    parser.add_argument(
        "--source-path",
        action="append",
        metavar="JSON_PATH",
        help="Limit silver to these source_bronze_path values (repeatable).",
    )
    parser.add_argument(
        "--compact",
        action="store_true",
        help="Run Delta compact() after scrape (slow on USB; off by default).",
    )
    parser.add_argument(
        "--skip-end-count",
        action="store_true",
        help="Skip final DuckDB pending count (overnight loops; prints estimate).",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.source_path and len(args.source_path) > 1:
        print(
            "Multiple --source-path not supported yet; pass one path.", file=sys.stderr
        )
        return 2
    if args.source_path:
        args.source_path = args.source_path[0]
    else:
        args.source_path = None
    return run_scrape(args)


if __name__ == "__main__":
    raise SystemExit(main())
