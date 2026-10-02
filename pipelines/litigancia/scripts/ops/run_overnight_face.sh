#!/usr/bin/env bash
# Loop face scrape (residential IP) until nothing left to scrape.
#
# Usage:
#   ./scripts/ops/run_overnight_face.sh
#   MAX_CDS=10000 WORKERS=10 DELAY_SEC=0.5 ./scripts/ops/run_overnight_face.sh
set -euo pipefail

source "$(cd "$(dirname "$0")" && pwd)/lib/common.sh"
htdr_cd_repo
htdr_ensure_logs

STAMP="$(date +%Y%m%d-%H%M%S)"
LOG_FILE="${LOG_FILE:-logs/face-${STAMP}.log}"
LATEST_LINK="logs/face-latest.log"
PID_FILE="logs/face.pid"

MAX_CDS="${MAX_CDS:-10000}"
WORKERS="${WORKERS:-10}"
DELAY_SEC="${DELAY_SEC:-0.5}"
FETCH_BATCH="${FETCH_BATCH:-500}"
DELTA_BATCH="${DELTA_BATCH:-200}"

ln -sf "$(basename "$LOG_FILE")" "$LATEST_LINK"

pending_count() {
  uv run python scripts/face/scraping/scrape_face_to_bronze.py --dry-run 2>/dev/null \
    | awk -F': ' '/without bronze face/{gsub(/,/,"",$2); print $2}'
}

parse_scraped_count() {
  grep 'Run finished. Scraped=' "$LOG_FILE" 2>/dev/null | tail -1 \
    | sed -E 's/.*Scraped=([0-9,]+).*/\1/' | tr -d ','
}

last_pending_from_log() {
  grep 'Silver cds without bronze face:' "$LOG_FILE" 2>/dev/null | tail -1 \
    | awk -F': ' '{gsub(/,/,"",$2); print $2}'
}

{
  echo "=== Face scrape overnight (residential) ==="
  echo "started_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "repo=$REPO_ROOT"
  echo "log=$LOG_FILE"
  echo "MAX_CDS=$MAX_CDS WORKERS=$WORKERS DELAY_SEC=$DELAY_SEC"
  echo "FETCH_BATCH=$FETCH_BATCH DELTA_BATCH=$DELTA_BATCH"
  htdr_log_env_paths
  echo "pending_start=$(pending_count)"
  echo "---"
} >>"$LOG_FILE"

export PYTHONUNBUFFERED=1
ITER=0
PENDING_START="$(pending_count)"

if [[ "$PENDING_START" -eq 0 ]]; then
  echo "No pending face cds at start; done." >>"$LOG_FILE"
  rm -f "$PID_FILE"
  exit 0
fi

while true; do
  ITER=$((ITER + 1))
  {
    echo ""
    echo "=== iteration ${ITER} utc=$(date -u +%Y-%m-%dT%H:%M:%SZ) ==="
  } >>"$LOG_FILE"

  set +e
  caffeinate -dims uv run python scripts/face/scraping/scrape_face_to_bronze.py \
    --max-cds "$MAX_CDS" \
    --workers "$WORKERS" \
    --delay-sec "$DELAY_SEC" \
    --fetch-batch "$FETCH_BATCH" \
    --delta-batch "$DELTA_BATCH" \
    --skip-end-count >>"$LOG_FILE" 2>&1
  EXIT_CODE=$?
  set -e

  LAST_PENDING="$(last_pending_from_log)"
  LAST_PENDING="${LAST_PENDING:-1}"
  if [[ "$LAST_PENDING" -eq 0 ]]; then
    echo "No pending face cds; done." >>"$LOG_FILE"
    break
  fi

  SCRAPED="$(parse_scraped_count)"
  SCRAPED="${SCRAPED:-0}"

  if [[ "$SCRAPED" -eq 0 ]]; then
    {
      echo "---"
      echo "finished_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
      echo "exit_code=${EXIT_CODE}"
      echo "reason=no cds scraped in iteration ${ITER}"
      echo "run_exit_code=${EXIT_CODE}"
    } >>"$LOG_FILE"
    rm -f "$PID_FILE"
    exit "$([[ "$EXIT_CODE" -eq 0 ]] && echo 0 || echo 1)"
  fi

  if [[ "$EXIT_CODE" -ne 0 ]]; then
    echo "warning: scrape exited ${EXIT_CODE} but scraped=${SCRAPED}; continuing" >>"$LOG_FILE"
  fi
done

{
  echo "---"
  echo "finished_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "exit_code=0"
  echo "iterations=${ITER}"
  echo "pending_start=${PENDING_START}"
  echo "pending_end=$(pending_count)"
} >>"$LOG_FILE"

rm -f "$PID_FILE"
exit 0
