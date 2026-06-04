#!/usr/bin/env bash
# Aegis kid-side updater. The kid setup command installs this on each child's Mac and runs it on a
# schedule via a LaunchAgent. This Chromium build does NOT auto-update force-installed extensions
# in-session, so this checks the guardian for a newer extension version and, when the browser is
# CLOSED, clears the cached copy so the next launch installs the new version.
#
# It is intentionally GENTLE: it never interrupts a browser that is in use, and it backs up the
# preference files before editing them. Nothing here needs the repo — it is fully self-contained.
#
# Usage: kid-update-check.sh --endpoint http://<guardian>:2947 --profile <name>
set -uo pipefail

ENDPOINT=""; PROFILE=""
while [ $# -gt 0 ]; do
  case "$1" in
    --endpoint) ENDPOINT="$2"; shift 2 ;;
    --profile) PROFILE="$2"; shift 2 ;;
    *) shift ;;
  esac
done
[ -n "$ENDPOINT" ] && [ -n "$PROFILE" ] || { echo "usage: $0 --endpoint URL --profile NAME" >&2; exit 2; }

DEFAULT="$HOME/Library/Application Support/Chromium/Default"
log() { printf '%s aegis-update: %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"; }

# What version does the guardian advertise for this kid, and what's the extension id?
XML="$(curl -fsS -m 8 "$ENDPOINT/ext/$PROFILE/updates.xml" 2>/dev/null)" \
  || { log "guardian unreachable at $ENDPOINT — will retry later"; exit 0; }
EXT_ID="$(printf '%s' "$XML" | grep -oE "appid='[a-p]{32}'" | grep -oE '[a-p]{32}' | head -1)"
WANT="$(printf '%s' "$XML" | grep -oE "version='[0-9.]+'" | grep -oE '[0-9.]+' | head -1)"
[ "${#EXT_ID}" -eq 32 ] && [ -n "$WANT" ] || { log "could not read the guardian update manifest"; exit 0; }

# What version is installed in this Mac's Chromium profile?
EXTDIR="$DEFAULT/Extensions/$EXT_ID"
HAVE=""
if [ -d "$EXTDIR" ]; then
  HAVE="$(ls -1 "$EXTDIR" 2>/dev/null | sed -E 's/_[0-9]+$//' \
    | sort -t. -k1,1n -k2,2n -k3,3n -k4,4n | tail -1)"
fi
log "profile=$PROFILE want=$WANT have=${HAVE:-none}"
[ "$HAVE" = "$WANT" ] && exit 0  # already current

# Never disrupt a browser that's open — defer to a later run.
if pgrep -f "Chromium.app/Contents/MacOS/Chromium" >/dev/null 2>&1; then
  log "update $WANT pending, but the browser is open — deferring"
  exit 0
fi

# Make Chromium treat the extension as "unknown" so it re-fetches the new version on next launch.
# Editing Secure Preferences/Preferences with plutil keeps the JSON valid; we back up first.
log "clearing the cached extension so v$WANT installs on next launch"
ts="$(date +%Y%m%d%H%M%S)"
for pref in "$DEFAULT/Secure Preferences" "$DEFAULT/Preferences"; do
  [ -f "$pref" ] || continue
  cp "$pref" "$pref.aegis-bak-$ts" 2>/dev/null || true
  plutil -remove "extensions.settings.$EXT_ID" "$pref" >/dev/null 2>&1 || true
  plutil -remove "protection.macs.extensions.settings.$EXT_ID" "$pref" >/dev/null 2>&1 || true
done
rm -rf "$EXTDIR"
log "done — the next time the browser opens it installs v$WANT"
