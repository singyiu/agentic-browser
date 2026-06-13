#!/usr/bin/env bash
# Install the macOS managed-preferences policy that FORCE-INSTALLS, FORCE-PINS, and
# LOCKS the parental-control extension on the kid browser (stock Chromium,
# bundle id org.chromium.Chromium).
#
#   - installation_mode: force_installed  -> the user CANNOT disable or remove it
#   - toolbar_pin:       force_pinned      -> the icon is pinned to the toolbar, un-unpinnable
#   - "*": blocked                          -> no OTHER extensions can be installed
#
# A *mandatory* macOS policy must live in /Library/Managed Preferences (root-owned);
# a plain `defaults write` to the app domain is only "recommended" and will NOT lock.
#
# Prerequisite: run scripts/pack-extension.sh first (it writes extension-id.txt), and
# make sure the guardian is serving /ext/updates.xml.
#
# Usage:  sudo bash agent-backend/scripts/install-extension-policy.sh
# Set AEGIS_INSECURE_UPDATES=1 if chrome://policy reports the http update_url blocked.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_ROOT="$(dirname "$HERE")"

if [ -f "$BACKEND_ROOT/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  . "$BACKEND_ROOT/.env"
  set +a
fi

DIST_DIR="${GUARDIAN_EXT_DIST_DIR:-$BACKEND_ROOT/.chromium-dist}"
ID_FILE="$DIST_DIR/extension-id.txt"
GUARDIAN_PORT="${GUARDIAN_PORT:-2947}"
ENDPOINT="${GUARDIAN_ENDPOINT:-http://127.0.0.1:${GUARDIAN_PORT}}"
UPDATE_URL="$ENDPOINT/ext/updates.xml"
# Default to the real managed-preferences path; AEGIS_PLIST_PATH overrides it (for testing).
PLIST="${AEGIS_PLIST_PATH:-/Library/Managed Preferences/org.chromium.Chromium.plist}"

if [ ! -f "$ID_FILE" ]; then
  echo "ERROR: $ID_FILE not found — run scripts/pack-extension.sh first." >&2
  exit 1
fi
EXT_ID="$(tr -d '[:space:]' <"$ID_FILE")"
if [ "${#EXT_ID}" -ne 32 ]; then
  echo "ERROR: extension id in $ID_FILE looks invalid: '$EXT_ID'" >&2
  exit 1
fi

# Writing the real managed-preferences plist requires root.
if [ -z "${AEGIS_PLIST_PATH:-}" ] && [ "$(id -u)" -ne 0 ]; then
  echo "ERROR: run with sudo (writes $PLIST)." >&2
  # Quoted so the copy-paste suggestion survives spaces in the repo path.
  echo "  sudo bash \"$HERE/$(basename "$0")\"" >&2
  exit 1
fi

INSECURE_BLOCK=""
if [ "${AEGIS_INSECURE_UPDATES:-0}" = "1" ]; then
  INSECURE_BLOCK="  <key>ExtensionAllowInsecureUpdates</key>
  <true/>
"
fi

mkdir -p "$(dirname "$PLIST")"
cat >"$PLIST" <<PLISTXML
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>ExtensionSettings</key>
  <dict>
    <key>*</key>
    <dict>
      <key>installation_mode</key>
      <string>blocked</string>
      <key>blocked_install_message</key>
      <string>Extensions are managed by your guardian.</string>
    </dict>
    <key>$EXT_ID</key>
    <dict>
      <key>installation_mode</key>
      <string>force_installed</string>
      <key>update_url</key>
      <string>$UPDATE_URL</string>
      <key>toolbar_pin</key>
      <string>force_pinned</string>
    </dict>
  </dict>
$INSECURE_BLOCK</dict>
</plist>
PLISTXML

plutil -lint "$PLIST" >/dev/null

if [ "$(id -u)" -eq 0 ]; then
  chown root:wheel "$PLIST"
  chmod 644 "$PLIST"
  # Flush the managed-preferences cache so Chromium picks up the policy on next launch.
  killall cfprefsd 2>/dev/null || true
fi

echo "Installed extension policy -> $PLIST"
echo "  force-installed + force-pinned + locked: $EXT_ID"
echo "  update_url: $UPDATE_URL"
echo "Relaunch Chromium, then check chrome://policy and chrome://extensions."
