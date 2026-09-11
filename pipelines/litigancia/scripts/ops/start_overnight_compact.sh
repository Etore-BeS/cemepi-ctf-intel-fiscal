#!/usr/bin/env bash
# Start compact in background; all output -> logs/compact-*.log
set -euo pipefail

source "$(cd "$(dirname "$0")" && pwd)/lib/common.sh"
htdr_cd_repo
htdr_ensure_logs

STAMP="$(date +%Y%m%d-%H%M%S)"
LOG_FILE="logs/compact-${STAMP}.log"
PID_FILE="logs/compact.pid"

if [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  echo "Already running (pid $(cat "$PID_FILE")). Log: logs/compact-latest.log"
  exit 1
fi

export LOG_FILE
nohup "$PIPELINE_ROOT/scripts/ops/run_overnight_compact.sh" "$@" >/dev/null 2>&1 &
echo $! >"$PID_FILE"
ln -sf "$(basename "$LOG_FILE")" logs/compact-latest.log

echo "Started compact in background."
echo "  pid=$(cat "$PID_FILE")"
echo "  log=$LOG_FILE"
echo "  tail -f $LOG_FILE"
