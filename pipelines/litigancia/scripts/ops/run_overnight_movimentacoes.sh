#!/usr/bin/env bash
# Bronze face → silver movimentacoes. Large backlog: one bronze stream pass.
# Small tail (<=5000): targeted DuckDB fetch only.
#
# Usage:
#   ./scripts/ops/run_overnight_movimentacoes.sh
#   PARENT_BATCH_SIZE=2000 FLUSH_EVERY_PARENTS=2000 ./scripts/ops/run_overnight_movimentacoes.sh
set -euo pipefail

source "$(cd "$(dirname "$0")" && pwd)/lib/common.sh"
htdr_cd_repo
htdr_ensure_logs

STAMP="$(date +%Y%m%d-%H%M%S)"
LOG_FILE="${LOG_FILE:-logs/movimentacoes-${STAMP}.log}"
LATEST_LINK="logs/movimentacoes-latest.log"
PID_FILE="logs/movimentacoes.pid"

PARENT_BATCH_SIZE="${PARENT_BATCH_SIZE:-2000}"
RAM_THRESHOLD="${RAM_THRESHOLD:-200000}"
FLUSH_EVERY_PARENTS="${FLUSH_EVERY_PARENTS:-2000}"
MERGE_CHUNK_SIZE="${MERGE_CHUNK_SIZE:-50000}"
TAIL_THRESHOLD="${TAIL_THRESHOLD:-5000}"
MAX_RESTARTS="${MAX_RESTARTS:-50}"

ln -sf "$(basename "$LOG_FILE")" "$LATEST_LINK"

pending_count() {
  uv run python scripts/movimentacoes/transforming/create_silver_movimentacoes.py --dry-run 2>/dev/null \
    | awk -F'=' '/^pending_work=/{print $2; exit}'
}

run_python() {
  local pending="$1"

  if [[ "$pending" -le "$TAIL_THRESHOLD" ]]; then
    echo "mode=tail pending=${pending}" >>"$LOG_FILE"
    caffeinate -dims uv run python scripts/movimentacoes/transforming/create_silver_movimentacoes.py \
      --skip-compact \
      --tail-only \
      --parent-batch-size 100 \
      --flush-every-parents 50 \
      --ram-threshold 10000 \
      --merge-chunk-size 5000
  else
    echo "mode=stream pending=${pending}" >>"$LOG_FILE"
    caffeinate -dims uv run python scripts/movimentacoes/transforming/create_silver_movimentacoes.py \
      --skip-compact \
      --parent-batch-size "$PARENT_BATCH_SIZE" \
      --ram-threshold "$RAM_THRESHOLD" \
      --flush-every-parents "$FLUSH_EVERY_PARENTS" \
      --merge-chunk-size "$MERGE_CHUNK_SIZE"
  fi
}

{
  echo "=== Movimentacoes silver overnight ==="
  echo "started_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "repo=$REPO_ROOT"
  echo "log=$LOG_FILE"
  echo "PARENT_BATCH_SIZE=$PARENT_BATCH_SIZE"
  echo "RAM_THRESHOLD=$RAM_THRESHOLD"
  echo "FLUSH_EVERY_PARENTS=$FLUSH_EVERY_PARENTS"
  echo "MERGE_CHUNK_SIZE=$MERGE_CHUNK_SIZE"
  echo "TAIL_THRESHOLD=$TAIL_THRESHOLD"
  echo "MAX_RESTARTS=$MAX_RESTARTS"
  htdr_log_env_paths
  echo "pending_start=$(pending_count)"
  echo "---"
} >>"$LOG_FILE"

export PYTHONUNBUFFERED=1
ATTEMPT=0
PENDING_START="$(pending_count)"

while true; do
  PENDING="$(pending_count)"
  {
    echo ""
    echo "=== attempt $((ATTEMPT + 1)) pending=${PENDING} utc=$(date -u +%Y-%m-%dT%H:%M:%SZ) ==="
  } >>"$LOG_FILE"

  if [[ "$PENDING" -eq 0 ]]; then
    echo "No pending bronze parent processes; done." >>"$LOG_FILE"
    break
  fi

  set +e
  run_python "$PENDING" >>"$LOG_FILE" 2>&1
  EXIT_CODE=$?
  set -e

  PENDING_AFTER="$(pending_count)"

  if [[ "$EXIT_CODE" -eq 0 && "$PENDING_AFTER" -eq 0 ]]; then
    break
  fi

  ATTEMPT=$((ATTEMPT + 1))
  {
    echo "attempt ${ATTEMPT} exited ${EXIT_CODE}; pending ${PENDING} -> ${PENDING_AFTER}"
    if [[ "$EXIT_CODE" -eq 137 ]]; then
      echo "hint=OOM (137). Lower PARENT_BATCH_SIZE / FLUSH_EVERY_PARENTS / RAM_THRESHOLD."
    fi
  } >>"$LOG_FILE"

  if [[ "$PENDING_AFTER" -ge "$PENDING" && "$EXIT_CODE" -ne 0 ]]; then
    echo "error: no progress on attempt ${ATTEMPT} (exit ${EXIT_CODE}); stopping." >>"$LOG_FILE"
    rm -f "$PID_FILE"
    exit 1
  fi

  if [[ "$ATTEMPT" -ge "$MAX_RESTARTS" ]]; then
    echo "error: hit MAX_RESTARTS=${MAX_RESTARTS}; stopping." >>"$LOG_FILE"
    rm -f "$PID_FILE"
    exit 1
  fi
done

{
  echo "---"
  echo "finished_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "exit_code=0"
  echo "attempts=$((ATTEMPT + 1))"
  echo "pending_start=${PENDING_START}"
  echo "pending_end=0"
  echo "note=Run optimize separately: uv run python scripts/maintenance/optimize_delta_lake.py --vacuum --max-concurrent-tasks 1 --table silver_layer/movimentacoes_delta"
} >>"$LOG_FILE"

rm -f "$PID_FILE"
exit 0
