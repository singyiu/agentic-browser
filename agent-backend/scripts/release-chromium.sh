#!/usr/bin/env bash
# Package the locally-built Chromium.app into a distributable artifact the guardian serves to
# kid Macs over the LAN. Run this once per browser build — a DEVELOPER/CI task, not something a
# parent ever does. Produces, in the guardian's dist dir (served at /dist/*):
#   browser.zip             the zipped Chromium.app (ditto, bundle-safe — preserves symlinks/xattrs)
#   chromium-manifest.json  {version, bundle_id, app_name, sha256, size} — kid installers verify this
#
# The kid bootstrapper downloads browser.zip, checks sha256 against the manifest, unzips into
# /Applications, and de-quarantines it so Gatekeeper allows this self-built browser to launch.
#
# Usage:
#   bash agent-backend/scripts/release-chromium.sh [--app PATH] [--sign] [--publish vTAG]
#     --app PATH     override the built-app path (default: mac/src/out/Release/Chromium.app)
#     --sign         ad-hoc re-sign the app before zipping (only if the build left it unsigned)
#     --publish vTAG also upload the artifacts to a GitHub release (needs the `gh` CLI)
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_ROOT="$(dirname "$HERE")"
REPO_ROOT="$(cd "$BACKEND_ROOT/.." && pwd)"

APP="$REPO_ROOT/mac/src/out/Release/Chromium.app"
SIGN=0
PUBLISH_TAG=""
while [ $# -gt 0 ]; do
  case "$1" in
    --app) APP="$2"; shift 2 ;;
    --sign) SIGN=1; shift ;;
    --publish) PUBLISH_TAG="$2"; shift 2 ;;
    -h|--help) sed -n '2,18p' "$0"; exit 0 ;;
    *) echo "Unknown arg: $1" >&2; exit 2 ;;
  esac
done

# Dist dir the guardian serves from (matches GUARDIAN_EXT_DIST_DIR / config.ext_dist_dir).
DIST_DIR="${GUARDIAN_EXT_DIST_DIR:-$BACKEND_ROOT/.chromium-dist}"
OUT_ZIP="$DIST_DIR/browser.zip"
OUT_MANIFEST="$DIST_DIR/chromium-manifest.json"

ok()  { printf '  \033[32m✓\033[0m %s\n' "$*"; }
warn(){ printf '  \033[33m!\033[0m %s\n' "$*"; }
die() { printf '  \033[31m✗\033[0m %s\n' "$*" >&2; exit 1; }

[ "$(uname -s)" = "Darwin" ] || die "release-chromium.sh runs on macOS (it packages a .app bundle)."
[ -d "$APP" ] || die "Chromium.app not found at: $APP  (build it first, or pass --app PATH)."

PLIST="$APP/Contents/Info.plist"
[ -f "$PLIST" ] || die "Info.plist not found in $APP."
read_plist() { /usr/libexec/PlistBuddy -c "Print :$1" "$PLIST" 2>/dev/null || true; }
VERSION="$(read_plist CFBundleShortVersionString)"; VERSION="${VERSION:-0.0.0}"
BUNDLE_ID="$(read_plist CFBundleIdentifier)"; BUNDLE_ID="${BUNDLE_ID:-org.chromium.Chromium}"
APP_NAME="$(basename "$APP")"
ok "App: $APP_NAME  version $VERSION  ($BUNDLE_ID)"

if [ "$SIGN" = "1" ]; then
  echo "  Ad-hoc signing (this can take a minute)…"
  codesign --force --deep --sign - "$APP" || die "codesign failed."
  ok "Ad-hoc signed."
fi
if codesign --verify --deep --strict "$APP" >/dev/null 2>&1; then
  ok "Code signature verifies."
else
  warn "App is not validly signed — the kid installer de-quarantines it so it still launches."
fi

mkdir -p "$DIST_DIR"
echo "  Zipping $APP_NAME → browser.zip (this can take a minute)…"
rm -f "$OUT_ZIP"
ditto -c -k --sequesterRsrc --keepParent "$APP" "$OUT_ZIP" || die "ditto zip failed."
SHA="$(shasum -a 256 "$OUT_ZIP" | awk '{print $1}')"
SIZE="$(stat -f%z "$OUT_ZIP" 2>/dev/null || wc -c <"$OUT_ZIP")"
ok "Zipped ($((SIZE / 1048576)) MB), sha256 ${SHA:0:16}…"

cat >"$OUT_MANIFEST" <<JSON
{
  "version": "$VERSION",
  "bundle_id": "$BUNDLE_ID",
  "app_name": "$APP_NAME",
  "sha256": "$SHA",
  "size": $SIZE
}
JSON
ok "Wrote manifest → $OUT_MANIFEST"

if [ -n "$PUBLISH_TAG" ]; then
  command -v gh >/dev/null 2>&1 || die "--publish needs the GitHub CLI (gh)."
  gh release create "$PUBLISH_TAG" "$OUT_ZIP" "$OUT_MANIFEST" \
       --title "Aegis browser $VERSION" --notes "Chromium $VERSION" 2>/dev/null \
    || gh release upload "$PUBLISH_TAG" "$OUT_ZIP" "$OUT_MANIFEST" --clobber
  ok "Published to GitHub release $PUBLISH_TAG."
fi

printf '\n\033[1m✓ Browser published.\033[0m The guardian serves it at /dist/browser.zip + /dist/manifest.json.\n'
printf 'Kid Macs get it automatically when you run “Add a kid” in the console.\n'
