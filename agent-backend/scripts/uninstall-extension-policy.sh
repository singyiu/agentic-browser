#!/usr/bin/env bash
# Remove the managed-preferences policy installed by install-extension-policy.sh,
# restoring normal/dev behavior (the extension is no longer force-installed/locked).
# Pair with AEGIS_DEV_UNPACKED=1 launches for development.
#
# Usage:  sudo bash agent-backend/scripts/uninstall-extension-policy.sh
set -euo pipefail

PLIST="${AEGIS_PLIST_PATH:-/Library/Managed Preferences/org.chromium.Chromium.plist}"

if [ -z "${AEGIS_PLIST_PATH:-}" ] && [ "$(id -u)" -ne 0 ]; then
  echo "ERROR: run with sudo (removes $PLIST)." >&2
  exit 1
fi

if [ -f "$PLIST" ]; then
  rm -f "$PLIST"
  echo "Removed $PLIST"
else
  echo "No policy at $PLIST (nothing to remove)."
fi

if [ "$(id -u)" -eq 0 ]; then
  killall cfprefsd 2>/dev/null || true
fi
echo "Relaunch Chromium to apply. The extension is no longer managed by policy."
