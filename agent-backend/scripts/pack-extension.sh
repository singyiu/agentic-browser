#!/usr/bin/env bash
# Pack the parental-control extension into a signed CRX + an Omaha update manifest,
# so the kid browser can FORCE-INSTALL it via enterprise policy (force_installed +
# force_pinned). The signing key is stable (agent-backend/.secrets/aegis-ext.pem),
# so the extension ID never changes across re-packs.
#
# The guardian token + endpoint are baked into the CRX at pack time (the running CRX is
# immutable, unlike the dev unpacked load). Re-run this whenever the token rotates or the
# extension code/version changes, then bump the version in extension/manifest.json.
#
# Defaults (no flags) read GUARDIAN_TOKEN / GUARDIAN_ENDPOINT from .env and write to
# .chromium-dist/ — unchanged from before. The guardian also calls this PER KID during
# enrollment, passing that kid's token, the LAN endpoint, and a per-profile output dir:
#
#   --token VALUE       guardian token baked into the CRX   (default: GUARDIAN_TOKEN)
#   --endpoint URL      guardian endpoint baked into the CRX (default: GUARDIAN_ENDPOINT or localhost)
#   --out DIR           output dir for the artifacts          (default: GUARDIAN_EXT_DIST_DIR or .chromium-dist)
#   --chromium PATH     Chromium binary used to pack          (default: the locally-built Chromium)
#   --profile NAME      informational label for the messages  (optional)
#
# Artifacts (all git-ignored) land in the output dir:
#   aegis.crx          the signed extension
#   updates.xml        the update manifest (served at /ext[/<profile>]/updates.xml)
#   extension-id.txt   the 32-char ID (read by install-extension-policy.sh)
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_ROOT="$(dirname "$HERE")"
REPO_ROOT="$(cd "$BACKEND_ROOT/.." && pwd)"

# --- explicit overrides (take precedence over .env) -----------------------------
ARG_TOKEN=""; ARG_ENDPOINT=""; ARG_OUT=""; ARG_CHROME=""; ARG_PROFILE=""
while [ $# -gt 0 ]; do
  case "$1" in
    --token) ARG_TOKEN="$2"; shift 2 ;;
    --endpoint) ARG_ENDPOINT="$2"; shift 2 ;;
    --out) ARG_OUT="$2"; shift 2 ;;
    --chromium) ARG_CHROME="$2"; shift 2 ;;
    --profile) ARG_PROFILE="$2"; shift 2 ;;
    -h|--help) sed -n '2,29p' "$0"; exit 0 ;;
    *) echo "Unknown arg: $1" >&2; exit 2 ;;
  esac
done

# Load .env so GUARDIAN_TOKEN/ENDPOINT match the running guardian service (defaults only).
if [ -f "$BACKEND_ROOT/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  . "$BACKEND_ROOT/.env"
  set +a
fi

CHROME="${ARG_CHROME:-$REPO_ROOT/mac/src/out/Release/Chromium.app/Contents/MacOS/Chromium}"
EXT_DIR="$REPO_ROOT/extension"
PEM="$BACKEND_ROOT/.secrets/aegis-ext.pem"
DIST_DIR="${ARG_OUT:-${GUARDIAN_EXT_DIST_DIR:-$BACKEND_ROOT/.chromium-dist}}"
BUILD_DIR="$DIST_DIR/aegis-build"
PYTHON="$BACKEND_ROOT/.venv/bin/python"

GUARDIAN_PORT="${GUARDIAN_PORT:-2947}"
ENDPOINT="${ARG_ENDPOINT:-${GUARDIAN_ENDPOINT:-http://127.0.0.1:${GUARDIAN_PORT}}}"
TOKEN="${ARG_TOKEN:-${GUARDIAN_TOKEN:-}}"

if [ ! -x "$CHROME" ]; then
  echo "Built Chromium not found at $CHROME — build it first (scripts/build.sh)." >&2
  exit 1
fi

# --- signing key (defines the extension ID) -------------------------------------
mkdir -p "$BACKEND_ROOT/.secrets"
if [ ! -f "$PEM" ]; then
  echo "No signing key at $PEM — generating one (the extension ID will change)." >&2
  openssl genrsa -out "$PEM" 2048 2>/dev/null
  chmod 600 "$PEM"
fi

pubkey_b64() { openssl rsa -in "$PEM" -pubout -outform DER 2>/dev/null | base64 | tr -d '\n'; }
EXT_ID="$(openssl rsa -in "$PEM" -pubout -outform DER 2>/dev/null \
  | openssl dgst -sha256 -binary | xxd -p -c256 | head -c 32 | tr '0-9a-f' 'a-p')"

# The unpacked load (manifest "key") and the CRX (signing key) must derive the SAME
# ID, or the policy force-install won't match. Fail loudly on drift.
MANIFEST_KEY="$("$PYTHON" -c "import json,sys;print(json.load(open(sys.argv[1])).get('key',''))" "$EXT_DIR/manifest.json")"
if [ "$MANIFEST_KEY" != "$(pubkey_b64)" ]; then
  echo "ERROR: extension/manifest.json \"key\" does not match $PEM." >&2
  echo "Set manifest \"key\" to this value (then commit) so the unpacked ID matches the CRX:" >&2
  echo "  $(pubkey_b64)" >&2
  exit 1
fi

VERSION="$("$PYTHON" -c "import json,sys;print(json.load(open(sys.argv[1]))['version'])" "$EXT_DIR/manifest.json")"

if [ -z "${TOKEN:-}" ]; then
  echo "WARNING: token is empty — the baked guardian-config.json will not authenticate." >&2
fi

# --- build a clean copy with the token + endpoint baked in ----------------------
mkdir -p "$DIST_DIR"
rm -rf "$BUILD_DIR" "$DIST_DIR/aegis-build.crx"
mkdir -p "$BUILD_DIR"
# Copy the extension verbatim except the dev placeholder config and OS cruft.
rsync -a --exclude 'guardian-config.json' --exclude '.DS_Store' "$EXT_DIR"/ "$BUILD_DIR"/
# Proper JSON serialization (quotes/backslashes in values must not corrupt the file);
# values travel via env, never argv, so the token cannot show up in `ps`.
AEGIS_BAKE_TOKEN="$TOKEN" AEGIS_BAKE_ENDPOINT="$ENDPOINT" "$PYTHON" -c '
import json, os, sys
sys.stdout.write(
    json.dumps(
        {"token": os.environ["AEGIS_BAKE_TOKEN"], "endpoint": os.environ["AEGIS_BAKE_ENDPOINT"]}
    )
    + "\n"
)
' >"$BUILD_DIR/guardian-config.json"

# --- pack (CRX3, signed with the stable key) ------------------------------------
# --pack-extension writes "<BUILD_DIR>.crx" beside the build dir, then exits.
"$CHROME" --pack-extension="$BUILD_DIR" --pack-extension-key="$PEM" \
  --no-sandbox --no-message-box >/dev/null 2>&1 || true
if [ ! -f "$DIST_DIR/aegis-build.crx" ]; then
  echo "ERROR: pack failed — $DIST_DIR/aegis-build.crx was not produced." >&2
  exit 1
fi
mv -f "$DIST_DIR/aegis-build.crx" "$DIST_DIR/aegis.crx"
rm -rf "$BUILD_DIR"

# --- update manifest + id file --------------------------------------------------
# A per-profile pack is served at /ext/<profile>/*, so its codebase must include the profile
# segment; the default pack keeps the legacy /ext/aegis.crx path.
CRX_URL="$ENDPOINT/ext${ARG_PROFILE:+/$ARG_PROFILE}/aegis.crx"
cat >"$DIST_DIR/updates.xml" <<XML
<?xml version='1.0' encoding='UTF-8'?>
<gupdate xmlns='http://www.google.com/update2/response' protocol='2.0'>
  <app appid='$EXT_ID'>
    <updatecheck codebase='$CRX_URL' version='$VERSION' />
  </app>
</gupdate>
XML
printf '%s\n' "$EXT_ID" >"$DIST_DIR/extension-id.txt"

echo "Packed extension v$VERSION${ARG_PROFILE:+ for profile '$ARG_PROFILE'}"
echo "  ID:        $EXT_ID"
echo "  CRX:       $DIST_DIR/aegis.crx"
echo "  updates:   $DIST_DIR/updates.xml (codebase $CRX_URL)"
