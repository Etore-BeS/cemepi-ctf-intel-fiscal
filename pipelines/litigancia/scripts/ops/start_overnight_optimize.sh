#!/usr/bin/env bash
# Start datalake Delta optimize in background; output -> logs/optimize-delta-*.log
#
# Usage:
#   ./scripts/ops/start_overnight_optimize.sh
#   ./scripts/ops/start_overnight_optimize.sh --z-order --vacuum
#   ./scripts/ops/start_overnight_optimize.sh --test   # smoke test (coletas_delta only)
set -euo pipefail

source "$(cd "$(dirname "$0")" && pwd)/lib/common.sh"
htdr_cd_repo
htdr_ensure_logs

STAMP="$(date +%Y%m%d-%H%M%S)"
LOG_FILE="logs/optimize-delta-${STAMP}.log"
PID_FILE="logs/optimize-delta.pid"

if [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  echo "Already running (pid $(cat "$PID_FILE")). Log: logs/optimize-delta-latest.log"
  exit 1
fi

export LOG_FILE
nohup "$PIPELINE_ROOT/scripts/ops/run_overnight_optimize.sh" "$@" >/dev/null 2>&1 &
echo $! >"$PID_FILE"
ln -sf "$(basename "$LOG_FILE")" logs/optimize-delta-latest.log

echo "Started Delta optimize in background."
echo "  pid=$(cat "$PID_FILE")"
echo "  log=$LOG_FILE"
echo "  tail -f logs/optimize-delta-latest.log"
