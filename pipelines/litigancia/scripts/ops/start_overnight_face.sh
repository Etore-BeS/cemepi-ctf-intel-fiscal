#!/usr/bin/env bash
# Start face scrape loop in background.
set -euo pipefail

source "$(cd "$(dirname "$0")" && pwd)/lib/common.sh"
htdr_cd_repo
htdr_ensure_logs

STAMP="$(date +%Y%m%d-%H%M%S)"
LOG_FILE="logs/face-${STAMP}.log"
PID_FILE="logs/face.pid"

if [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  echo "Already running (pid $(cat "$PID_FILE")). Log: logs/face-latest.log"
  exit 1
fi

export LOG_FILE
nohup "$PIPELINE_ROOT/scripts/ops/run_overnight_face.sh" >/dev/null 2>&1 &
echo $! >"$PID_FILE"
ln -sf "$(basename "$LOG_FILE")" logs/face-latest.log

echo "Started face scrape loop in background."
echo "  pid=$(cat "$PID_FILE")"
echo "  log=$LOG_FILE"
echo "  tail -f $LOG_FILE"
echo ""
echo "Tune via env: MAX_CDS=10000 WORKERS=10 DELAY_SEC=0.5 ./scripts/ops/start_overnight_face.sh"
