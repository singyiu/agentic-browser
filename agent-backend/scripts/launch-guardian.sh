#!/usr/bin/env bash
# Start the parental-control guardian service (headless Claude classifier).
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_ROOT="$(dirname "$HERE")"

export CLAUDE_CONFIG_DIR="${CLAUDE_CONFIG_DIR:-$BACKEND_ROOT/claude-config}"
PY="$BACKEND_ROOT/.venv/bin/python"

if [ ! -x "$PY" ]; then
  echo "venv not found. Run: uv sync" >&2
  exit 1
fi

cd "$BACKEND_ROOT"  # so the service loads ./.env and writes ./data
exec "$PY" -m agent_backend.guardian
