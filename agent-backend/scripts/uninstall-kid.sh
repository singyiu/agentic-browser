#!/usr/bin/env bash
# Aegis — remove the locked browser from THIS child's Mac. Enter the Mac password when asked
# (removing the lock policy needs admin). Self-contained: it needs no repo, so it can be run on a
# kid Mac via the guardian's /dist/uninstall-kid.sh.
set -uo pipefail

AGENT_LABEL="com.aegis.kidbrowser"
AGENT_PLIST="$HOME/Library/LaunchAgents/$AGENT_LABEL.plist"
SUPPORT="$HOME/Library/Application Support/Aegis"
POLICY_PLIST="/Library/Managed Preferences/org.chromium.Chromium.plist"
APP="/Applications/Chromium.app"

bold(){ printf '\033[1m%s\033[0m\n' "$*"; }
ok(){   printf '  \033[32m✓\033[0m %s\n' "$*"; }
warn(){ printf '  \033[33m!\033[0m %s\n' "$*"; }

bold "Aegis — removing the locked browser from this Mac"

# 1. Stop and remove the auto-update agent.
launchctl bootout "gui/$(id -u)/$AGENT_LABEL" 2>/dev/null || true
rm -f "$AGENT_PLIST"
rm -rf "$SUPPORT"
ok "Auto-update agent removed."

# 2. Remove the lock policy (needs admin).
if [ -f "$POLICY_PLIST" ]; then
  echo "  Removing the lock policy needs this Mac's password."
  if sudo rm -f "$POLICY_PLIST"; then
    sudo killall cfprefsd 2>/dev/null || true
    ok "Lock policy removed."
  else
    warn "Could not remove the lock policy ($POLICY_PLIST)."
  fi
else
  ok "No lock policy present."
fi

# 3. Quit and remove the browser.
pkill -f "Chromium.app/Contents/MacOS/Chromium" 2>/dev/null || true
sleep 1
if [ -d "$APP" ]; then
  rm -rf "$APP" && ok "Removed $APP." || warn "Could not remove $APP."
fi

printf '\n'
bold "✓ Done. This Mac is no longer managed by Aegis."
echo "Browsing data under ~/Library/Application Support/Chromium was left in place —"
echo "delete it manually if you want a clean slate."
