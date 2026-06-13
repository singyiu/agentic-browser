#!/usr/bin/env bash
# Aegis Guardian — one-touch installer for the PARENT (guardian) Mac.
#
# Double-click this file in Finder (or run:  bash install-guardian.command).
# It is SAFE TO RE-RUN: it keeps any secret/PIN you already have and only fills
# in what is missing.
#
# What it does (macOS):
#   1. Pre-flight — checks macOS, the `uv` tool (installs it if missing, with
#      Python 3.12), and the Claude Code CLI.
#   2. Builds the Python environment (uv sync).
#   3. Writes agent-backend/.env — auto-generates a strong GUARDIAN_TOKEN, binds
#      the guardian to the LAN (so kid Macs can reach it), and captures your
#      Claude Max login token.
#   4. Allows the guardian through the macOS firewall (so kid Macs can connect).
#   5. Installs the always-on background service (starts at login, restarts on crash).
#   6. Opens the setup console in your browser.
#
# The only things it asks YOU for: your Claude Max login (in a browser) and your
# Mac password (for the firewall rule and the background service).
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_ROOT="$(dirname "$HERE")"
REPO_ROOT="$(cd "$BACKEND_ROOT/.." && pwd)"
ENV_FILE="$BACKEND_ROOT/.env"
ENV_EXAMPLE="$BACKEND_ROOT/.env.example"

# --- pretty output -------------------------------------------------------------
bold() { printf '\033[1m%s\033[0m\n' "$*"; }
ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$*"; }
errln(){ printf '  \033[31m✗\033[0m %s\n' "$*" >&2; }
step() { printf '\n\033[1m[%s/6]\033[0m \033[1m%s\033[0m\n' "$1" "$2"; }
die()  { errln "$*"; printf '\nSetup stopped. Fix the issue above, then run this again.\n' >&2; exit 1; }

# --- .env helpers (update-or-append a KEY=VALUE line) --------------------------
env_get() {  # env_get KEY  -> current value (last wins), empty if unset
  [ -f "$ENV_FILE" ] || return 0
  # `|| true`: an absent key is a normal answer, not an error (pipefail + set -e).
  grep -E "^[[:space:]]*$1=" "$ENV_FILE" 2>/dev/null | tail -1 | cut -d= -f2- || true
}
env_set() {  # env_set KEY VALUE  (atomic; no special-char interpretation of VALUE)
  local key="$1" val="$2" tmp
  tmp="$(mktemp "${TMPDIR:-/tmp}/aegis-env.XXXXXX")" || die "could not create a temp file"
  if [ -f "$ENV_FILE" ] && grep -qE "^[[:space:]]*${key}=" "$ENV_FILE"; then
    awk -v k="$key" -v v="$val" 'index($0,k"=")==1 || $0 ~ "^[[:space:]]*"k"=" {print k"="v; next} {print}' \
      "$ENV_FILE" >"$tmp"
  else
    [ -f "$ENV_FILE" ] && cat "$ENV_FILE" >"$tmp"
    printf '%s=%s\n' "$key" "$val" >>"$tmp"
  fi
  mv "$tmp" "$ENV_FILE"
}

detect_lan_ip() {
  local ifc ip
  ifc="$(route -n get default 2>/dev/null | awk '/interface:/{print $2; exit}')"
  if [ -n "${ifc:-}" ]; then
    ip="$(ipconfig getifaddr "$ifc" 2>/dev/null || true)"
    [ -n "${ip:-}" ] && { printf '%s\n' "$ip"; return 0; }
  fi
  for ifc in en0 en1 en2 en3; do
    ip="$(ipconfig getifaddr "$ifc" 2>/dev/null || true)"
    [ -n "${ip:-}" ] && { printf '%s\n' "$ip"; return 0; }
  done
  return 1
}

allow_firewall() {  # best-effort; $1 = real python binary path
  local fw="/usr/libexec/ApplicationFirewall/socketfilterfw" py="$1" state
  [ -x "$fw" ] || { warn "Firewall tool not found; skipping."; return 0; }
  state="$("$fw" --getglobalstate 2>/dev/null || true)"
  case "$state" in
    *"State = 0"*|*disabled*) ok "macOS firewall is off — no rule needed."; return 0 ;;
  esac
  printf '  The macOS firewall is on. Allowing the guardian needs your Mac password.\n'
  if sudo "$fw" --add "$py" >/dev/null 2>&1 && sudo "$fw" --unblockapp "$py" >/dev/null 2>&1; then
    ok "Guardian allowed through the firewall."
  else
    warn "Could not add the firewall rule automatically. If kid Macs can't connect, run:"
    printf '      sudo "%s" --add "%s"\n      sudo "%s" --unblockapp "%s"\n' "$fw" "$py" "$fw" "$py"
  fi
}

# ==============================================================================
clear 2>/dev/null || true
bold "Aegis Guardian — installer for the parent Mac"
printf 'Repo: %s\n' "$REPO_ROOT"

# --- 1. Pre-flight -------------------------------------------------------------
step 1 "Checking your Mac"
[ "$(uname -s)" = "Darwin" ] || die "This installer is for macOS only."
ok "macOS detected."

if ! command -v uv >/dev/null 2>&1; then
  warn "'uv' (the Python installer) is missing — installing it now…"
  # --proto '=https' --tlsv1.2: never follow a downgraded redirect for piped-to-shell code.
  curl --proto '=https' --tlsv1.2 -LsSf https://astral.sh/uv/install.sh | sh \
    || die "Could not install 'uv'. See https://docs.astral.sh/uv/."
  export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
fi
command -v uv >/dev/null 2>&1 || die "'uv' still not found on PATH. Open a new Terminal and run this again."
ok "uv is installed ($(uv --version 2>/dev/null | head -1))."

if ! command -v claude >/dev/null 2>&1; then
  warn "The Claude Code CLI ('claude') is not installed."
  printf '    The guardian uses it to classify pages. Install it, then re-run this script:\n'
  printf '      npm install -g @anthropic-ai/claude-code      (needs Node.js: https://nodejs.org)\n'
fi
command -v node >/dev/null 2>&1 || warn "Node.js not found — the 'claude' CLI needs it (https://nodejs.org)."

# --- 2. Python environment -----------------------------------------------------
step 2 "Building the Python environment"
( cd "$BACKEND_ROOT" && uv sync ) || die "uv sync failed. Scroll up for the error."
PYBIN="$("$BACKEND_ROOT/.venv/bin/python" -c 'import sys; print(sys.executable)' 2>/dev/null || true)"
[ -n "$PYBIN" ] || die "The Python environment was not created at $BACKEND_ROOT/.venv."
ok "Python environment ready."

# --- 3. Configuration (.env) ---------------------------------------------------
step 3 "Configuring the guardian"
[ -f "$ENV_FILE" ] || { cp "$ENV_EXAMPLE" "$ENV_FILE" && ok "Created agent-backend/.env from the template."; }

if [ -z "$(env_get GUARDIAN_TOKEN)" ]; then
  env_set GUARDIAN_TOKEN "$(openssl rand -hex 32)"
  ok "Generated a strong GUARDIAN_TOKEN (the shared secret kid Macs use)."
else
  ok "Keeping your existing GUARDIAN_TOKEN."
fi

# Only set on first run: a parent who deliberately re-bound the guardian (e.g. back to
# 127.0.0.1 for a single-Mac setup) keeps their choice across re-runs.
if [ -z "$(env_get GUARDIAN_HOST)" ]; then
  env_set GUARDIAN_HOST "0.0.0.0"
  ok "Guardian will accept connections from kid Macs on your network (GUARDIAN_HOST=0.0.0.0)."
else
  ok "Keeping your existing GUARDIAN_HOST=$(env_get GUARDIAN_HOST)."
fi

LAN_IP="$(detect_lan_ip || true)"
PORT="$(env_get GUARDIAN_PORT)"; PORT="${PORT:-2947}"
[ -n "$LAN_IP" ] && ok "This Mac's network address: $LAN_IP (kids will connect to http://$LAN_IP:$PORT)" \
                || warn "Could not detect a LAN address — connect this Mac to Wi-Fi/Ethernet."

if [ -z "$(env_get CLAUDE_CODE_OAUTH_TOKEN)" ]; then
  printf '\n  The guardian needs your Claude Max login (a one-time token).\n'
  if command -v claude >/dev/null 2>&1 && [ -t 0 ]; then
    printf '  A browser window will open to sign in; a token will be printed here afterward.\n\n'
    claude setup-token || warn "claude setup-token did not complete."
    printf '\n  Paste the token shown above and press Return (or leave blank to do this later):\n  > '
    IFS= read -r TOKEN || TOKEN=""
    if [ -n "$TOKEN" ]; then env_set CLAUDE_CODE_OAUTH_TOKEN "$TOKEN"; ok "Saved your Claude login token."; \
      else warn "No token entered — set CLAUDE_CODE_OAUTH_TOKEN in agent-backend/.env before the guardian can classify."; fi
  else
    warn "Set this up later: run 'claude setup-token' and paste the result into CLAUDE_CODE_OAUTH_TOKEN in agent-backend/.env."
  fi
else
  ok "Keeping your existing Claude login token."
fi

# --- 4. Firewall ---------------------------------------------------------------
step 4 "Network access (firewall)"
allow_firewall "$PYBIN"

# --- 5. Always-on service ------------------------------------------------------
step 5 "Installing the always-on guardian service"
bash "$HERE/install-guardian-service.sh" || die "Service install failed. Scroll up for the error (often a bad/missing Claude token)."

# --- 6. Open the console -------------------------------------------------------
step 6 "Opening the setup console"
open "http://localhost:$PORT/setup" 2>/dev/null || true

printf '\n'
bold "✓ Guardian installed."
printf '┌──────────────────────────────────────────────────────────────────┐\n'
printf '  Set your parent PIN in the browser window that just opened.\n'
[ -n "$LAN_IP" ] && printf '  Console (this Mac):   http://localhost:%s/\n  On your network:      http://%s:%s/\n' "$PORT" "$LAN_IP" "$PORT" || true
printf '  Next: in the console, click “Add a kid” to set up each child’s Mac.\n'
printf '└──────────────────────────────────────────────────────────────────┘\n'
printf '\nManage the service:  bash "%s/uninstall-guardian-service.sh"  to remove.\n' "$HERE"
printf 'Update later:        bash "%s/update-guardian.sh"\n' "$HERE"
