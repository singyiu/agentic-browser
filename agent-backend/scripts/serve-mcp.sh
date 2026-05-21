#!/usr/bin/env bash
# Run the browser-control MCP server standalone over stdio (for debugging, e.g.
# with the MCP inspector). Normally the runner spawns this for you.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_ROOT="$(dirname "$HERE")"
PY="$BACKEND_ROOT/.venv/bin/python"

export CHROMIUM_CDP_URL="${CHROMIUM_CDP_URL:-http://127.0.0.1:9222}"
exec "$PY" -m agent_backend.mcp_server
