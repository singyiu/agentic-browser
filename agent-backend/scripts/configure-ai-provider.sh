#!/usr/bin/env bash
# Aegis Guardian — choose the headless AI provider (Claude or Codex) and set up its auth.
#
# The guardian and the browser agent run on a subscription, not a metered API key:
#   • claude → Claude Max subscription via the `claude` CLI   (token in .env)
#   • codex  → ChatGPT subscription via the `codex` CLI        (auth.json under CODEX_HOME)
#
# Interactive:  bash scripts/configure-ai-provider.sh
# Scripted:     bash scripts/configure-ai-provider.sh --provider codex --non-interactive
#               (the installer passes --no-restart; it starts the service itself)
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_ROOT="$(dirname "$HERE")"
ENV_FILE="$BACKEND_ROOT/.env"
ENV_EXAMPLE="$BACKEND_ROOT/.env.example"
CODEX_HOME_DIR="$BACKEND_ROOT/codex-config"

# --- pretty output -------------------------------------------------------------
bold() { printf '\033[1m%s\033[0m\n' "$*"; }
ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$*"; }
errln(){ printf '  \033[31m✗\033[0m %s\n' "$*" >&2; }
die()  { errln "$*"; exit 1; }

# --- .env helpers (update-or-append a KEY=VALUE line; atomic) -------------------
env_get() {
  [ -f "$ENV_FILE" ] || return 0
  grep -E "^[[:space:]]*$1=" "$ENV_FILE" 2>/dev/null | tail -1 | cut -d= -f2- || true
}
env_set() {
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

# --- args ----------------------------------------------------------------------
PROVIDER=""
INTERACTIVE=1
RESTART=1
while [ $# -gt 0 ]; do
  case "$1" in
    --provider) PROVIDER="${2:-}"; shift 2 ;;
    --provider=*) PROVIDER="${1#*=}"; shift ;;
    --non-interactive) INTERACTIVE=0; shift ;;
    --no-restart) RESTART=0; shift ;;
    -h|--help)
      sed -n '2,12p' "$0"; exit 0 ;;
    *) die "unknown argument: $1 (use --provider claude|codex, --non-interactive, --no-restart)" ;;
  esac
done

[ -f "$ENV_FILE" ] || { cp "$ENV_EXAMPLE" "$ENV_FILE" && ok "Created agent-backend/.env from the template."; }

# --- choose provider -----------------------------------------------------------
if [ -z "$PROVIDER" ]; then
  if [ "$INTERACTIVE" = "1" ] && [ -t 0 ]; then
    bold "Aegis — choose your AI provider"
    printf '    [1] Claude  (Claude Max subscription via the claude CLI)\n'
    printf '    [2] Codex   (ChatGPT subscription via the codex CLI)\n'
    printf '  Choice [1]: '
    IFS= read -r CHOICE || CHOICE=""
    case "$CHOICE" in 2) PROVIDER="codex" ;; *) PROVIDER="claude" ;; esac
  else
    PROVIDER="$(env_get AEGIS_AI_PROVIDER)"; PROVIDER="${PROVIDER:-claude}"
  fi
fi
case "$PROVIDER" in
  claude|codex) ;;
  *) die "provider must be 'claude' or 'codex' (got '$PROVIDER')" ;;
esac

# --- Claude (Claude Max subscription) ------------------------------------------
configure_claude() {
  if ! command -v claude >/dev/null 2>&1; then
    warn "The Claude Code CLI ('claude') is not installed."
    printf '    Install it, then re-run this script:\n      npm install -g @anthropic-ai/claude-code\n'
    die "claude CLI missing"
  fi
  if [ -n "$(env_get CLAUDE_CODE_OAUTH_TOKEN)" ]; then
    ok "Keeping your existing Claude login token."
  elif [ "$INTERACTIVE" = "1" ] && [ -t 0 ]; then
    printf '\n  A browser window opens to sign in; a token prints here afterward.\n\n'
    claude setup-token || warn "claude setup-token did not complete."
    printf '\n  Paste the token shown above and press Return (blank to do this later):\n  > '
    IFS= read -r TOKEN || TOKEN=""
    if [ -n "$TOKEN" ]; then
      env_set CLAUDE_CODE_OAUTH_TOKEN "$TOKEN"; ok "Saved your Claude login token."
    else
      warn "No token entered — set CLAUDE_CODE_OAUTH_TOKEN in .env before the guardian can classify."
    fi
  else
    warn "Set this up later: run 'claude setup-token' and paste it into CLAUDE_CODE_OAUTH_TOKEN in .env."
  fi
  env_set AEGIS_AI_PROVIDER claude
  ok "AI provider set to claude."
  command -v claude >/dev/null 2>&1 && claude --version >/dev/null 2>&1 \
    && ok "Claude CLI reachable." || warn "Could not run 'claude --version'."
}

# --- Codex (ChatGPT subscription) ----------------------------------------------
configure_codex() {
  if ! command -v codex >/dev/null 2>&1; then
    warn "The Codex CLI ('codex') is not installed."
    printf '    Install it, then re-run this script:\n      npm install -g @openai/codex\n'
    die "codex CLI missing"
  fi
  mkdir -p "$CODEX_HOME_DIR"; chmod 700 "$CODEX_HOME_DIR" 2>/dev/null || true
  # Store credentials in auth.json (not the OS keychain) so the headless service can read them.
  if ! grep -qs 'cli_auth_credentials_store' "$CODEX_HOME_DIR/config.toml" 2>/dev/null; then
    printf 'cli_auth_credentials_store = "file"\n' >>"$CODEX_HOME_DIR/config.toml"
  fi
  if [ -f "$CODEX_HOME_DIR/auth.json" ]; then
    ok "Codex is already signed in (auth.json present)."
  elif [ "$INTERACTIVE" = "1" ] && [ -t 0 ]; then
    printf '\n  A browser window opens to sign in to your ChatGPT account.\n\n'
    CODEX_HOME="$CODEX_HOME_DIR" codex login || warn "codex login did not complete."
  else
    warn "Sign in later:  CODEX_HOME=\"$CODEX_HOME_DIR\" codex login"
  fi
  [ -f "$CODEX_HOME_DIR/auth.json" ] \
    || die "Codex is not signed in (no auth.json). Run:  CODEX_HOME=\"$CODEX_HOME_DIR\" codex login"
  chmod 600 "$CODEX_HOME_DIR/auth.json" 2>/dev/null || true
  env_set AEGIS_AI_PROVIDER codex
  env_set CODEX_HOME "$CODEX_HOME_DIR"
  ok "AI provider set to codex (auth in $CODEX_HOME_DIR/auth.json)."

  # Tiny end-to-end probe so misconfig surfaces now, not on the first page load.
  local model; model="$(env_get GUARDIAN_MODEL)"; model="${model:-gpt-5-codex}"
  printf '  Testing a tiny Codex round-trip (model %s)…\n' "$model"
  if printf 'Reply with exactly: OK' | CODEX_HOME="$CODEX_HOME_DIR" codex exec - \
       --skip-git-repo-check --sandbox read-only \
       --color never --ephemeral -m "$model" >/dev/null 2>&1; then
    ok "Codex responded."
  else
    warn "Codex test call failed — verify 'codex login' and that your ChatGPT plan exposes '$model'"
    warn "(override with GUARDIAN_MODEL / GUARDIAN_AGENT_MODEL in .env)."
  fi
}

# --- restart the guardian so it picks up the new provider ----------------------
restart_guardian() {
  [ "$RESTART" = "1" ] || return 0  # installer starts the service itself
  local label="com.aegis.guardian" uid; uid="$(id -u)"
  if [ "$(uname -s)" = "Darwin" ] && launchctl print "gui/$uid/$label" >/dev/null 2>&1; then
    launchctl kickstart -k "gui/$uid/$label" >/dev/null 2>&1 \
      && ok "Guardian restarted." || warn "Could not restart the guardian — restart it manually."
  elif command -v systemctl >/dev/null 2>&1 && systemctl --user status aegis-guardian >/dev/null 2>&1; then
    systemctl --user restart aegis-guardian \
      && ok "Guardian restarted." || warn "Could not restart the guardian — restart it manually."
  else
    warn "Guardian service not running yet — it will use this provider once started/installed."
  fi
}

# --- run -----------------------------------------------------------------------
if [ "$PROVIDER" = "codex" ]; then configure_codex; else configure_claude; fi
restart_guardian

printf '\n'
bold "✓ AI provider configured: $PROVIDER"
