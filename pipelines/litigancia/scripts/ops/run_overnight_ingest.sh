#!/usr/bin/env bash
# Loop silver ingest (1 path per iteration) until checkpoint is clear or a run fails.
#
# Usage:
#   ./scripts/ops/run_overnight_ingest.sh
#   MAX_ITEMS=2 ./scripts/ops/run_overnight_ingest.sh
set -euo pipefail

source "$(cd "$(dirname "$0")" && pwd)/lib/common.sh"
htdr_cd_repo
htdr_ensure_logs

STAMP="$(date +%Y%m%d-%H%M%S)"
LOG_FILE="${LOG_FILE:-logs/ingest-${STAMP}.log}"
LATEST_LINK="logs/ingest-latest.log"
PID_FILE="logs/ingest.pid"
MAX_ITEMS="${MAX_ITEMS:-1}"

ln -sf "$(basename "$LOG_FILE")" "$LATEST_LINK"

pending_count() {
  htdr_uv_python -c "
from config.scripts import load_transform_module
print(len(load_transform_module('create_silver_layer').get_pending_bronze_paths()))
" 2>/dev/null
}

{
  echo "=== Silver ingest overnight (loop, max-items=${MAX_ITEMS}) ==="
  echo "started_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "repo=$REPO_ROOT"
  echo "log=$LOG_FILE"
  htdr_log_env_paths
  echo "pending_start=$(pending_count)"
  echo "---"
} >>"$LOG_FILE"

export PYTHONUNBUFFERED=1

ITER=0
while true; do
  PENDING="$(pending_count)"
  ITER=$((ITER + 1))
  {
    echo ""
    echo "=== iteration ${ITER} pending=${PENDING} utc=$(date -u +%Y-%m-%dT%H:%M:%SZ) ==="
  } >>"$LOG_FILE"

  if [[ "$PENDING" -eq 0 ]]; then
    echo "No pending silver paths; done." >>"$LOG_FILE"
    break
  fi

  set +e
  caffeinate -dims uv run python scripts/processos/run_new_data_to_silver.py \
    --skip-sqlite --max-items "$MAX_ITEMS" >>"$LOG_FILE" 2>&1
  EXIT_CODE=$?
  set -e

  PENDING_AFTER="$(pending_count)"
  if [[ "$PENDING_AFTER" -ge "$PENDING" ]]; then
    {
      echo "---"
      echo "finished_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
      echo "exit_code=1"
      echo "reason=pending count did not decrease (${PENDING} -> ${PENDING_AFTER})"
      echo "run_exit_code=${EXIT_CODE}"
      echo "pending_end=${PENDING_AFTER}"
    } >>"$LOG_FILE"
    rm -f "$PID_FILE"
    exit 1
  fi

  if [[ "$EXIT_CODE" -ne 0 ]]; then
    echo "warning: run exited ${EXIT_CODE} but pending decreased (${PENDING} -> ${PENDING_AFTER}); continuing" >>"$LOG_FILE"
  fi
done

{
  echo "---"
  echo "finished_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "exit_code=0"
  echo "iterations=${ITER}"
  echo "pending_end=0"
} >>"$LOG_FILE"

rm -f "$PID_FILE"
exit 0
