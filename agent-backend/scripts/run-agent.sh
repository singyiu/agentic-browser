#!/usr/bin/env bash
# Run an autonomous browser task on the configured provider (AEGIS_AI_PROVIDER: claude|codex).
# Sets the isolated per-provider config dirs so the backend's CLI config/auth stay separate
# from your personal ~/.claude and ~/.codex.
#
#   bash scripts/run-agent.sh "Open example.com and report the H1"
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_ROOT="$(dirname "$HERE")"

export CLAUDE_CONFIG_DIR="${CLAUDE_CONFIG_DIR:-$BACKEND_ROOT/claude-config}"
export CODEX_HOME="${CODEX_HOME:-$BACKEND_ROOT/codex-config}"
PY="$BACKEND_ROOT/.venv/bin/python"

if [ ! -x "$PY" ]; then
  echo "venv not found. Run: uv sync" >&2
  exit 1
fi

cd "$BACKEND_ROOT"  # so the runner picks up ./.env
exec "$PY" -m agent_backend.runner "$@"
