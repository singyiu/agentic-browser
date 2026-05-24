#!/usr/bin/env bash
# Remove the always-on guardian service installed by install-guardian-service.sh.
# Stops the running process and deletes the generated unit. Your .env and data/ are untouched.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LABEL="com.aegis.guardian"   # macOS launchd label
UNIT_NAME="aegis-guardian.service"   # Linux systemd unit name

OS="$(uname -s)"
case "$OS" in
  Darwin)
    PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
    DOMAIN="gui/$(id -u)"
    launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
    rm -f "$PLIST"
    echo "Removed launchd agent $LABEL and $PLIST."
    ;;
  Linux)
    UNIT="$HOME/.config/systemd/user/$UNIT_NAME"
    systemctl --user disable --now "$UNIT_NAME" 2>/dev/null || true
    rm -f "$UNIT"
    systemctl --user daemon-reload 2>/dev/null || true
    echo "Removed systemd unit $UNIT_NAME and $UNIT."
    ;;
  *)
    echo "Unsupported OS: $OS." >&2
    exit 1
    ;;
esac
