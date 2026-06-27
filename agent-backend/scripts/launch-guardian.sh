#!/usr/bin/env bash
# Start the parental-control guardian service (headless AI classifier: Claude or Codex,
# selected by AEGIS_AI_PROVIDER in .env).
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_ROOT="$(dirname "$HERE")"

# Isolate each provider's CLI config/auth in a project-local dir (claude provider uses
# CLAUDE_CONFIG_DIR; codex provider uses CODEX_HOME). Both are set regardless of the active
# provider — the unused one is harmless — and an env-supplied value always wins.
export CLAUDE_CONFIG_DIR="${CLAUDE_CONFIG_DIR:-$BACKEND_ROOT/claude-config}"
export CODEX_HOME="${CODEX_HOME:-$BACKEND_ROOT/codex-config}"
PY="$BACKEND_ROOT/.venv/bin/python"

if [ ! -x "$PY" ]; then
  echo "venv not found. Run: uv sync" >&2
  exit 1
fi

cd "$BACKEND_ROOT"  # so the service loads ./.env and writes ./data
exec "$PY" -m agent_backend.guardian
