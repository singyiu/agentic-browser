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
# Chromium process log, tailed into Loki by the observability stack. Touch it so a
# Docker bind-mount sees a file (not a directory) even on first launch.
CHROMIUM_LOG_FILE="${CHROMIUM_LOG_PATH:-$PROFILE/chrome_debug.log}"

if [ ! -x "$CHROME" ]; then
  echo "Built Chromium not found at $CHROME — build it first (../scripts/build.sh)." >&2
  exit 1
fi

# GUARDIAN_ENDPOINT lets this (browser) machine point at a guardian running on another
# LAN host; the default is localhost, so a single-machine setup is unchanged.
ENDPOINT="${GUARDIAN_ENDPOINT:-http://127.0.0.1:${GUARDIAN_PORT}}"

mkdir -p "$(dirname "$CHROMIUM_LOG_FILE")"
touch "$CHROMIUM_LOG_FILE"

# Extension loading has two modes:
#   AEGIS_DEV_UNPACKED=1 -> developer load via --load-extension (live-editable, but the kid
#       can toggle it off at chrome://extensions). The token config is written into the
#       extension dir at launch, as before.
#   default (locked)     -> the extension is FORCE-INSTALLED, force-pinned, and locked by the
#       managed-preferences policy (scripts/install-extension-policy.sh), which installs it
#       from the guardian's /ext/updates.xml. The token is baked into the CRX by
#       scripts/pack-extension.sh, so nothing is loaded or written here.
EXT_FLAGS=()
POLICY_PLIST="/Library/Managed Preferences/org.chromium.Chromium.plist"
if [ "${AEGIS_DEV_UNPACKED:-0}" = "1" ]; then
  printf '{"token":"%s","endpoint":"%s"}\n' \
    "${GUARDIAN_TOKEN:-}" "$ENDPOINT" >"$EXT_DIR/guardian-config.json"
  EXT_FLAGS=(--load-extension="$EXT_DIR" --disable-extensions-except="$EXT_DIR")
  echo "Launching Chromium (CDP :$PORT) — DEV unpacked extension (NOT locked)"
else
  echo "Launching Chromium (CDP :$PORT) — extension managed by enterprise policy"
  if [ ! -f "$POLICY_PLIST" ]; then
    echo "  NOTE: no managed policy at $POLICY_PLIST." >&2
    echo "  Run scripts/pack-extension.sh, then: sudo bash scripts/install-extension-policy.sh" >&2
    echo "  (or set AEGIS_DEV_UNPACKED=1 for a developer load)." >&2
  fi
fi
echo "Guardian endpoint: $ENDPOINT"
echo "Chromium log: $CHROMIUM_LOG_FILE"
# ${EXT_FLAGS[@]+...} expands to nothing when the array is empty (safe under set -u).
exec "$CHROME" \
  --remote-debugging-port="$PORT" \
  --user-data-dir="$PROFILE" \
  ${EXT_FLAGS[@]+"${EXT_FLAGS[@]}"} \
  --enable-logging \
  --log-file="$CHROMIUM_LOG_FILE" \
  --v=0 \
  --no-first-run \
  --no-default-browser-check \
  "$@"
