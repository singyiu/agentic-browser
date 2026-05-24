#!/usr/bin/env bash
# Install the guardian as an always-on background service: auto-start at login and
# auto-restart on crash. macOS -> launchd LaunchAgent; Linux -> systemd --user unit.
#
# It reuses scripts/launch-guardian.sh (which sets CLAUDE_CONFIG_DIR, loads ./.env, and
# writes ./data) as the exec target, and bakes the current PATH into the unit so the
# classifier's `claude`/`node` lookup works under the minimal launchd/systemd environment.
#
# Re-run this after switching Node versions (e.g. nvm) so the baked PATH stays valid.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_ROOT="$(dirname "$HERE")"
PY="$BACKEND_ROOT/.venv/bin/python"
LABEL="com.agentic-browser.guardian"   # macOS launchd label
UNIT_NAME="agentic-guardian.service"   # Linux systemd unit name

# --- Pre-flight ---------------------------------------------------------------
if [ ! -x "$PY" ]; then
  echo "venv not found at $PY. Run: uv sync" >&2
  exit 1
fi
if [ ! -f "$BACKEND_ROOT/.env" ]; then
  echo "No .env at $BACKEND_ROOT/.env — copy .env.example and set CLAUDE_CODE_OAUTH_TOKEN." >&2
  exit 1
fi
command -v claude >/dev/null 2>&1 || \
  echo "WARN: 'claude' not on PATH; the classifier spawns the Claude Code CLI." >&2
command -v node >/dev/null 2>&1 || \
  echo "WARN: 'node' not on PATH; the 'claude' CLI needs Node.js." >&2

# Guardian API port for the health probe (read-only parse of .env; never sourced).
PORT="$(sed -n -E 's/^[[:space:]]*GUARDIAN_PORT[[:space:]]*=[[:space:]]*["'\'']?([0-9]+).*/\1/p' \
  "$BACKEND_ROOT/.env" 2>/dev/null | tail -1)"
PORT="${PORT:-2947}"

# --- Helpers ------------------------------------------------------------------
render() {  # render <template-path> to stdout with placeholders substituted
  local content
  content="$(cat "$1")"
  content="${content//__LABEL__/$LABEL}"
  content="${content//__BACKEND_ROOT__/$BACKEND_ROOT}"
  content="${content//__PATH__/$PATH}"
  printf '%s\n' "$content"
}

guardian_health() {  # exit 0 if GET /health answers
  local url="http://127.0.0.1:$PORT/health"
  if command -v curl >/dev/null 2>&1; then
    curl -fsS -m 2 "$url" >/dev/null 2>&1
  else
    "$PY" -c 'import sys,urllib.request; urllib.request.urlopen(sys.argv[1],timeout=2).read()' \
      "$url" >/dev/null 2>&1
  fi
}

wait_healthy() {
  echo "Waiting for the guardian to answer /health on 127.0.0.1:$PORT ..."
  local i
  for i in $(seq 1 20); do
    if guardian_health; then echo "OK: guardian is running and healthy."; return 0; fi
    sleep 1
  done
  echo "ERROR: guardian did not become healthy within 20s." >&2
  echo "--- last 20 lines of data/guardian.err.log ---" >&2
  tail -n 20 "$BACKEND_ROOT/data/guardian.err.log" 2>/dev/null >&2 || echo "(no err log yet)" >&2
  return 1
}

# --- Install ------------------------------------------------------------------
OS="$(uname -s)"
case "$OS" in
  Darwin)
    PLIST_DIR="$HOME/Library/LaunchAgents"
    PLIST="$PLIST_DIR/$LABEL.plist"
    DOMAIN="gui/$(id -u)"
    mkdir -p "$PLIST_DIR"
    render "$BACKEND_ROOT/deploy/guardian.launchd.plist.template" > "$PLIST"
    echo "Wrote $PLIST"
    # Unload any existing job first. bootout is asynchronous, so wait for the label to
    # actually disappear before bootstrapping — otherwise bootstrap races it and fails.
    if launchctl print "$DOMAIN/$LABEL" >/dev/null 2>&1; then
      launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
      for _ in $(seq 1 20); do
        launchctl print "$DOMAIN/$LABEL" >/dev/null 2>&1 || break
        sleep 0.5
      done
    fi
    if ! launchctl bootstrap "$DOMAIN" "$PLIST"; then
      echo "ERROR: 'launchctl bootstrap $DOMAIN $PLIST' failed." >&2
      echo "Fix:   launchctl bootout $DOMAIN/$LABEL   # then re-run this script" >&2
      exit 1
    fi
    launchctl enable "$DOMAIN/$LABEL" 2>/dev/null || true
    launchctl kickstart "$DOMAIN/$LABEL" 2>/dev/null || true
    wait_healthy
    echo
    echo "Installed. The guardian starts at login and restarts on crash."
    echo "  status:  launchctl print $DOMAIN/$LABEL | grep -iE 'state|pid'"
    echo "  logs:    tail -f \"$BACKEND_ROOT\"/data/guardian.out.log \"$BACKEND_ROOT\"/data/guardian.err.log"
    echo "  remove:  bash \"$HERE/uninstall-guardian-service.sh\""
    ;;
  Linux)
    UNIT_DIR="$HOME/.config/systemd/user"
    UNIT="$UNIT_DIR/$UNIT_NAME"
    mkdir -p "$UNIT_DIR"
    render "$BACKEND_ROOT/deploy/guardian.systemd.service.template" > "$UNIT"
    echo "Wrote $UNIT"
    systemctl --user daemon-reload
    systemctl --user enable --now "$UNIT_NAME"
    wait_healthy
    echo
    echo "Installed. The guardian starts with your user session and restarts on crash."
    echo "  status:  systemctl --user status $UNIT_NAME"
    echo "  logs:    journalctl --user -u $UNIT_NAME -f"
    echo "  persist: loginctl enable-linger \"$USER\"   # keep running without an active login"
    echo "  remove:  bash \"$HERE/uninstall-guardian-service.sh\""
    ;;
  *)
    echo "Unsupported OS: $OS (only macOS/launchd and Linux/systemd are supported)." >&2
    exit 1
    ;;
esac
