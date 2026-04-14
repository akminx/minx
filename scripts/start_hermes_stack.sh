#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PYTHON_BIN="${PYTHON_BIN:-}"
if [[ -z "$PYTHON_BIN" ]]; then
  if [[ -x ".venv/bin/python" ]]; then
    PYTHON_BIN=".venv/bin/python"
  else
    PYTHON_BIN="python3"
  fi
fi

HOST="${MINX_HTTP_HOST:-127.0.0.1}"
CORE_PORT="${MINX_CORE_PORT:-8001}"
FINANCE_PORT="${MINX_FINANCE_PORT:-8000}"
MEALS_PORT="${MINX_MEALS_PORT:-8002}"
TRAINING_PORT="${MINX_TRAINING_PORT:-8003}"

export MINX_CORE_PORT="$CORE_PORT"
export MINX_FINANCE_PORT="$FINANCE_PORT"
export MINX_MEALS_PORT="$MEALS_PORT"
export MINX_TRAINING_PORT="$TRAINING_PORT"

cat <<INFO
Starting Minx MCP servers for Hermes harness:
- minx-finance  http://${HOST}:${FINANCE_PORT}
- minx-core     http://${HOST}:${CORE_PORT}
- minx-meals    http://${HOST}:${MEALS_PORT}
- minx-training http://${HOST}:${TRAINING_PORT}

Press Ctrl+C to stop all servers.
INFO

exec "$PYTHON_BIN" -m minx_mcp.launcher --transport http --servers minx-core minx-finance minx-meals minx-training
