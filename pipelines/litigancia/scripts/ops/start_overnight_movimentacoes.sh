#!/usr/bin/env bash
# Start movimentacoes silver transform loop in background.
set -euo pipefail

source "$(cd "$(dirname "$0")" && pwd)/lib/common.sh"
htdr_cd_repo
htdr_ensure_logs

STAMP="$(date +%Y%m%d-%H%M%S)"
LOG_FILE="logs/movimentacoes-${STAMP}.log"
PID_FILE="logs/movimentacoes.pid"

if [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  echo "Already running (pid $(cat "$PID_FILE")). Log: logs/movimentacoes-latest.log"
  exit 1
fi

export LOG_FILE
nohup "$PIPELINE_ROOT/scripts/ops/run_overnight_movimentacoes.sh" >/dev/null 2>&1 &
echo $! >"$PID_FILE"
ln -sf "$(basename "$LOG_FILE")" logs/movimentacoes-latest.log

echo "Started movimentacoes silver loop in background."
echo "  pid=$(cat "$PID_FILE")"
echo "  log=$LOG_FILE"
echo "  tail -f logs/movimentacoes-latest.log"
echo ""
echo "Tune via env: PARENT_BATCH_SIZE=2000 FLUSH_EVERY_PARENTS=2000 ./scripts/ops/start_overnight_movimentacoes.sh"
