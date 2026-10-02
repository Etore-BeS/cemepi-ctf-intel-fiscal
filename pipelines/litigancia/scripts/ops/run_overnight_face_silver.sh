#!/usr/bin/env bash
# Loop face silver transform until pending=0 or a wave fails to shrink pending.
#
# Usage:
#   ./scripts/ops/run_overnight_face_silver.sh
#   MAX_ROWS=50000 ./scripts/ops/run_overnight_face_silver.sh
set -euo pipefail

source "$(cd "$(dirname "$0")" && pwd)/lib/common.sh"
htdr_cd_repo
htdr_ensure_logs

STAMP="$(date +%Y%m%d-%H%M%S)"
LOG_FILE="${LOG_FILE:-logs/face-silver-${STAMP}.log}"
LATEST_LINK="logs/face-silver-latest.log"
PID_FILE="logs/face-silver.pid"

MAX_ROWS="${MAX_ROWS:-100000}"

ln -sf "$(basename "$LOG_FILE")" "$LATEST_LINK"

pending_count() {
  uv run python scripts/face/transforming/create_silver_face_layer.py --dry-run 2>/dev/null \
    | awk -F'=' '/^pending_work=/{print $2; exit}'
}

{
  echo "=== Face silver overnight ==="
  echo "started_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "repo=$REPO_ROOT"
  echo "log=$LOG_FILE"
  echo "MAX_ROWS=$MAX_ROWS"
  htdr_log_env_paths
  echo "Refreshing bronze row cache (slow once per overnight run)..."
  uv run python scripts/face/transforming/create_silver_face_layer.py --dry-run --refresh-counts >>"$LOG_FILE" 2>&1 || true
  echo "pending_start=$(pending_count)"
  echo "---"
} >>"$LOG_FILE"

export PYTHONUNBUFFERED=1
ITER=0
PENDING_START="$(pending_count)"

while true; do
  PENDING="$(pending_count)"
  ITER=$((ITER + 1))
  {
    echo ""
    echo "=== iteration ${ITER} pending=${PENDING} utc=$(date -u +%Y-%m-%dT%H:%M:%SZ) ==="
  } >>"$LOG_FILE"

  if [[ "$PENDING" -eq 0 ]]; then
    echo "No pending bronze face rows; done." >>"$LOG_FILE"
    break
  fi

  set +e
  caffeinate -dims uv run python scripts/face/transforming/create_silver_face_layer.py \
    --max-rows "$MAX_ROWS" \
    --skip-backfill >>"$LOG_FILE" 2>&1
  EXIT_CODE=$?
  set -e

  PENDING_AFTER="$(pending_count)"
  if [[ "$PENDING_AFTER" -ge "$PENDING" ]]; then
    {
      echo "---"
      echo "finished_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
      echo "exit_code=1"
      echo "reason=pending did not decrease (${PENDING} -> ${PENDING_AFTER})"
      echo "run_exit_code=${EXIT_CODE}"
    } >>"$LOG_FILE"
    rm -f "$PID_FILE"
    exit 1
  fi

  if [[ "$EXIT_CODE" -ne 0 ]]; then
    echo "warning: transform exited ${EXIT_CODE} but pending decreased; continuing" >>"$LOG_FILE"
  fi
done

{
  echo "---"
  echo "finished_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "exit_code=0"
  echo "iterations=${ITER}"
  echo "pending_start=${PENDING_START}"
  echo "pending_end=0"
} >>"$LOG_FILE"

rm -f "$PID_FILE"
exit 0
