#!/usr/bin/env bash
# Compact all Delta tables overnight with full logging under logs/.
#
# Usage:
#   ./scripts/ops/run_overnight_optimize.sh
#   ./scripts/ops/run_overnight_optimize.sh --z-order --vacuum
#   ./scripts/ops/run_overnight_optimize.sh --test
set -euo pipefail

source "$(cd "$(dirname "$0")" && pwd)/lib/common.sh"
htdr_cd_repo
htdr_ensure_logs

STAMP="$(date +%Y%m%d-%H%M%S)"
LOG_FILE="${LOG_FILE:-logs/optimize-delta-${STAMP}.log}"
LATEST_LINK="logs/optimize-delta-latest.log"
PID_FILE="logs/optimize-delta.pid"

ln -sf "$(basename "$LOG_FILE")" "$LATEST_LINK"

{
  echo "=== Delta lake optimize overnight ==="
  echo "started_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "repo=$REPO_ROOT"
  echo "log=$LOG_FILE"
  htdr_log_env_paths
  echo "command=uv run python scripts/maintenance/optimize_delta_lake.py --z-order --vacuum $*"
  echo "---"
} >>"$LOG_FILE"

export PYTHONUNBUFFERED=1

set +e
caffeinate -dims uv run python scripts/maintenance/optimize_delta_lake.py --z-order --vacuum "$@" \
  >>"$LOG_FILE" 2>&1
EXIT_CODE=$?
set -e

{
  echo "---"
  echo "finished_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "exit_code=$EXIT_CODE"
} >>"$LOG_FILE"

rm -f "$PID_FILE"
exit "$EXIT_CODE"
