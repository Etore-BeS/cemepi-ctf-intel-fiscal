#!/usr/bin/env bash
# Shared helpers for overnight wrapper scripts.
# Usage: source "$(dirname "$0")/lib/common.sh"

_HTDR_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -z "${PIPELINE_ROOT:-}" ]]; then
  PIPELINE_ROOT="$(cd "${_HTDR_LIB_DIR}/../../.." && pwd)"
fi
if [[ -z "${REPO_ROOT:-}" ]]; then
  REPO_ROOT="$(cd "${PIPELINE_ROOT}/../.." && pwd)"
fi

htdr_cd_repo() {
  cd "$PIPELINE_ROOT"
}

htdr_ensure_logs() {
  mkdir -p "$PIPELINE_ROOT/logs"
}

htdr_log_env_paths() {
  if [[ -f "$REPO_ROOT/.env" ]]; then
    grep -E '^(LAKE_ROOT|COLLECT_ROOT)=' "$REPO_ROOT/.env" || true
  else
    echo "warning=no .env file"
  fi
}

htdr_uv_python() {
  # Usage: htdr_uv_python scripts/foo.py --flag
  (cd "$PIPELINE_ROOT" && uv run python "$@")
}
