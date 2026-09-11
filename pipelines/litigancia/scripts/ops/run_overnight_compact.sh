#!/usr/bin/env bash
# Run global silver compact overnight with full logging under logs/.
# Usage:
#   ./scripts/ops/run_overnight_compact.sh              # default: 128 buckets + delta compact
#   ./scripts/ops/run_overnight_compact.sh --buckets 64   # extra args passed to compact_silver_processos.py
set -euo pipefail

source "$(cd "$(dirname "$0")" && pwd)/lib/common.sh"
htdr_cd_repo
htdr_ensure_logs

STAMP="$(date +%Y%m%d-%H%M%S)"
LOG_FILE="${LOG_FILE:-logs/compact-${STAMP}.log}"
LATEST_LINK="logs/compact-latest.log"
PID_FILE="logs/compact.pid"

ln -sf "$(basename "$LOG_FILE")" "$LATEST_LINK"

{
  echo "=== Silver compact overnight ==="
  echo "started_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "repo=$REPO_ROOT"
  echo "log=$LOG_FILE"
  htdr_log_env_paths
  echo "command=uv run python scripts/processos/transforming/compact_silver_processos.py $*"
  echo "---"
} >>"$LOG_FILE"

export PYTHONUNBUFFERED=1

# -d: prevent idle sleep, -i: prevent idle, -m: prevent disk sleep, -s: AC (when plugged in)
set +e
caffeinate -dims uv run python scripts/processos/transforming/compact_silver_processos.py "$@" \
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
