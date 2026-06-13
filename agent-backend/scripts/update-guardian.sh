#!/usr/bin/env bash
# Update the guardian to the latest code and restart it. Safe to re-run.
#   1. git pull (fast-forward only) in the repo
#   2. uv sync   (apply any dependency changes)
#   3. restart the always-on service (launchd on macOS, systemd --user on Linux)
#   4. wait for /health to confirm it came back up
#
# Usage:  bash agent-backend/scripts/update-guardian.sh
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_ROOT="$(dirname "$HERE")"
REPO_ROOT="$(cd "$BACKEND_ROOT/.." && pwd)"
LABEL="com.aegis.guardian"
UNIT_NAME="aegis-guardian.service"
PY="$BACKEND_ROOT/.venv/bin/python"

ok()  { printf '  \033[32m✓\033[0m %s\n' "$*"; }
warn(){ printf '  \033[33m!\033[0m %s\n' "$*"; }
die() { printf '  \033[31m✗\033[0m %s\n' "$*" >&2; exit 1; }
step(){ printf '\n\033[1m%s\033[0m\n' "$*"; }

PORT="$(sed -n -E 's/^[[:space:]]*GUARDIAN_PORT[[:space:]]*=[[:space:]]*["'\'']?([0-9]+).*/\1/p' \
  "$BACKEND_ROOT/.env" 2>/dev/null | tail -1 || true)"
PORT="${PORT:-2947}"

guardian_health() {
  "$PY" - "$PORT" <<'PY' >/dev/null 2>&1
import sys, urllib.request
urllib.request.urlopen(f"http://127.0.0.1:{sys.argv[1]}/health", timeout=2).read()
PY
}

step "1/4  Pulling the latest code"
git -C "$REPO_ROOT" pull --ff-only || die "git pull failed (local changes or no network?). Resolve, then re-run."
ok "Code up to date."

step "2/4  Updating dependencies"
( cd "$BACKEND_ROOT" && uv sync ) || die "uv sync failed."
ok "Dependencies in sync."

step "3/4  Restarting the guardian"
case "$(uname -s)" in
  Darwin)
    if launchctl print "gui/$(id -u)/$LABEL" >/dev/null 2>&1; then
      launchctl kickstart -k "gui/$(id -u)/$LABEL" || die "Could not restart the service."
      ok "Service restarted."
    else
      warn "Service not installed yet — run install-guardian.command first."
      exit 0
    fi
    ;;
  Linux)
    systemctl --user restart "$UNIT_NAME" || die "Could not restart the service."
    ok "Service restarted."
    ;;
  *) die "Unsupported OS: $(uname -s)" ;;
esac

step "4/4  Verifying"
for _ in $(seq 1 20); do
  if guardian_health; then ok "Guardian is healthy on port $PORT."; printf '\n\033[1m✓ Update complete.\033[0m\n'; exit 0; fi
  sleep 1
done
die "Guardian did not answer /health within 20s. Check: tail -n 40 \"$BACKEND_ROOT/data/guardian.err.log\""
