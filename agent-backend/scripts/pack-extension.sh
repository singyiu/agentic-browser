#!/usr/bin/env bash
# Pack the parental-control extension into a signed CRX + an Omaha update manifest,
# so the kid browser can FORCE-INSTALL it via enterprise policy (force_installed +
# force_pinned). The signing key is stable (agent-backend/.secrets/aegis-ext.pem),
# so the extension ID never changes across re-packs.
#
# The guardian token is baked into the CRX at pack time (the running CRX is immutable,
# unlike the dev unpacked load). Re-run this whenever the token rotates or the
# extension code/version changes, then bump the version in extension/manifest.json.
#
# Artifacts (all git-ignored) land in agent-backend/.chromium-dist/:
#   aegis.crx          the signed extension
#   updates.xml        the update manifest (served at /ext/updates.xml)
#   extension-id.txt   the 32-char ID (read by install-extension-policy.sh)
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_ROOT="$(dirname "$HERE")"
REPO_ROOT="$(cd "$BACKEND_ROOT/.." && pwd)"

# Load .env so GUARDIAN_TOKEN/ENDPOINT match the running guardian service.
if [ -f "$BACKEND_ROOT/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  . "$BACKEND_ROOT/.env"
  set +a
fi

CHROME="$REPO_ROOT/mac/src/out/Release/Chromium.app/Contents/MacOS/Chromium"
EXT_DIR="$REPO_ROOT/extension"
PEM="$BACKEND_ROOT/.secrets/aegis-ext.pem"
DIST_DIR="${GUARDIAN_EXT_DIST_DIR:-$BACKEND_ROOT/.chromium-dist}"
BUILD_DIR="$DIST_DIR/aegis-build"
PYTHON="$BACKEND_ROOT/.venv/bin/python"

GUARDIAN_PORT="${GUARDIAN_PORT:-2947}"
ENDPOINT="${GUARDIAN_ENDPOINT:-http://127.0.0.1:${GUARDIAN_PORT}}"

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

if [ -z "${GUARDIAN_TOKEN:-}" ]; then
  echo "WARNING: GUARDIAN_TOKEN is empty — the baked guardian-config.json will not authenticate." >&2
fi

# --- build a clean copy with the token baked in ---------------------------------
rm -rf "$BUILD_DIR" "$DIST_DIR/aegis-build.crx"
mkdir -p "$BUILD_DIR"
# Copy the extension verbatim except the dev placeholder config and OS cruft.
rsync -a --exclude 'guardian-config.json' --exclude '.DS_Store' "$EXT_DIR"/ "$BUILD_DIR"/
printf '{"token":"%s","endpoint":"%s"}\n' "${GUARDIAN_TOKEN:-}" "$ENDPOINT" \
  >"$BUILD_DIR/guardian-config.json"

# --- pack (CRX3, signed with the stable key) ------------------------------------
# --pack-extension writes "<BUILD_DIR>.crx" beside the build dir, then exits.
"$CHROME" --pack-extension="$BUILD_DIR" --pack-extension-key="$PEM" \
  --no-message-box >/dev/null 2>&1 || true
if [ ! -f "$DIST_DIR/aegis-build.crx" ]; then
  echo "ERROR: pack failed — $DIST_DIR/aegis-build.crx was not produced." >&2
  exit 1
fi
mv -f "$DIST_DIR/aegis-build.crx" "$DIST_DIR/aegis.crx"
rm -rf "$BUILD_DIR"

# --- update manifest + id file --------------------------------------------------
cat >"$DIST_DIR/updates.xml" <<XML
<?xml version='1.0' encoding='UTF-8'?>
<gupdate xmlns='http://www.google.com/update2/response' protocol='2.0'>
  <app appid='$EXT_ID'>
    <updatecheck codebase='$ENDPOINT/ext/aegis.crx' version='$VERSION' />
  </app>
</gupdate>
XML
printf '%s\n' "$EXT_ID" >"$DIST_DIR/extension-id.txt"

echo "Packed extension v$VERSION"
echo "  ID:        $EXT_ID"
echo "  CRX:       $DIST_DIR/aegis.crx"
echo "  updates:   $DIST_DIR/updates.xml (codebase $ENDPOINT/ext/aegis.crx)"
echo "Next: restart the guardian, then run: sudo bash $HERE/install-extension-policy.sh"
