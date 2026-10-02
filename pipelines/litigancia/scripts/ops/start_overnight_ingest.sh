#!/usr/bin/env bash
# Start ingest loop in background; output -> logs/ingest-*.log
#
# Usage:
#   ./scripts/ops/start_overnight_ingest.sh
#   MAX_ITEMS=1 ./scripts/ops/start_overnight_ingest.sh
set -euo pipefail

source "$(cd "$(dirname "$0")" && pwd)/lib/common.sh"
htdr_cd_repo
htdr_ensure_logs

STAMP="$(date +%Y%m%d-%H%M%S)"
LOG_FILE="logs/ingest-${STAMP}.log"
PID_FILE="logs/ingest.pid"

if [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  echo "Already running (pid $(cat "$PID_FILE")). Log: logs/ingest-latest.log"
  exit 1
fi

export LOG_FILE
nohup "$PIPELINE_ROOT/scripts/ops/run_overnight_ingest.sh" >/dev/null 2>&1 &
echo $! >"$PID_FILE"
ln -sf "$(basename "$LOG_FILE")" logs/ingest-latest.log

echo "Started ingest loop in background."
echo "  pid=$(cat "$PID_FILE")"
echo "  log=$LOG_FILE"
echo "  tail -f $LOG_FILE"
