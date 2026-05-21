#!/usr/bin/env bash
# Launch the built Chromium with the CDP endpoint the agent connects to.
# Extra args are passed through (e.g. a starting URL).
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_ROOT="$(dirname "$HERE")"
REPO_ROOT="$(cd "$BACKEND_ROOT/.." && pwd)"

CHROME="$REPO_ROOT/mac/src/out/Release/Chromium.app/Contents/MacOS/Chromium"
PORT="${CHROMIUM_CDP_PORT:-9222}"
PROFILE="${CHROMIUM_PROFILE:-$BACKEND_ROOT/.chromium-profile}"

if [ ! -x "$CHROME" ]; then
  echo "Built Chromium not found at $CHROME — build it first (../scripts/build.sh)." >&2
  exit 1
fi

echo "Launching Chromium with CDP on http://127.0.0.1:$PORT (profile: $PROFILE)"
exec "$CHROME" \
  --remote-debugging-port="$PORT" \
  --user-data-dir="$PROFILE" \
  --no-first-run \
  --no-default-browser-check \
  "$@"
