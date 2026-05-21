#!/usr/bin/env bash
# Launch the built Chromium with: CDP (for the agent runner) + the parental-control
# extension. Extra args are passed through (e.g. a starting URL).
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_ROOT="$(dirname "$HERE")"
REPO_ROOT="$(cd "$BACKEND_ROOT/.." && pwd)"

# Load .env so GUARDIAN_TOKEN matches the guardian service.
if [ -f "$BACKEND_ROOT/.env" ]; then
  set -a
  . "$BACKEND_ROOT/.env"
  set +a
fi

CHROME="$REPO_ROOT/mac/src/out/Release/Chromium.app/Contents/MacOS/Chromium"
PORT="${CHROMIUM_CDP_PORT:-9222}"
PROFILE="${CHROMIUM_PROFILE:-$BACKEND_ROOT/.chromium-profile}"
EXT_DIR="$REPO_ROOT/extension"
GUARDIAN_PORT="${GUARDIAN_PORT:-2947}"

if [ ! -x "$CHROME" ]; then
  echo "Built Chromium not found at $CHROME — build it first (../scripts/build.sh)." >&2
  exit 1
fi

# Hand the extension the backend token + endpoint (this file is git-ignored).
printf '{"token":"%s","endpoint":"http://127.0.0.1:%s"}\n' \
  "${GUARDIAN_TOKEN:-}" "$GUARDIAN_PORT" >"$EXT_DIR/guardian-config.json"

echo "Launching Chromium (CDP :$PORT) with the parental-control extension"
exec "$CHROME" \
  --remote-debugging-port="$PORT" \
  --user-data-dir="$PROFILE" \
  --load-extension="$EXT_DIR" \
  --disable-extensions-except="$EXT_DIR" \
  --no-first-run \
  --no-default-browser-check \
  "$@"
