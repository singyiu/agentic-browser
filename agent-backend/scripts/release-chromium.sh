#!/usr/bin/env bash
# Package the locally-built Chromium.app into a distributable artifact the guardian serves to
# kid Macs over the LAN. Run this once per browser build — a DEVELOPER/CI task, not something a
# parent ever does. Produces, in the guardian's dist dir (served at /dist/*):
#   browser.zip             the zipped Chromium.app (ditto, bundle-safe — preserves symlinks;
#                           deliberately NO xattr/__MACOSX sidecars, see the zipping step)
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

# Refuse to publish a bundle that can never run. All of Chromium's code lives in the framework
# binary (hundreds of MB); a tiny one means an incomplete build or a component build
# (is_component_build=true), whose code sits in dylibs OUTSIDE the bundle — it unpacks fine on
# the kid Mac but crashes at launch. Rebuild with is_component_build=false.
FW_BIN=""
for f in "$APP"/Contents/Frameworks/*.framework/Versions/*/*" Framework"; do
  [ -f "$f" ] && FW_BIN="$f" && break
done
[ -n "$FW_BIN" ] || die "No framework binary inside $APP_NAME — incomplete build?"
FW_MB=$(( $(stat -f%z "$FW_BIN") / 1048576 ))
[ "$FW_MB" -ge 100 ] \
  || die "Framework binary is only ${FW_MB} MB — incomplete or component build (code outside the bundle). Rebuild with is_component_build=false."
ok "Framework binary looks complete (${FW_MB} MB)."

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
# No --sequesterRsrc: it embeds AppleDouble (__MACOSX) sidecars carrying xattrs like
# com.apple.provenance, which macOS 14+ refuses to restore — ditto -x on the kid Mac then
# aborts with "Operation not permitted". The bundle needs no resource forks/xattrs
# (signatures are embedded in the binaries), so ship a plain zip.
ditto -c -k --keepParent "$APP" "$OUT_ZIP" || die "ditto zip failed."
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

  # Pin the release to the repo this checkout actually pushes to, and authenticate as the account
  # that owns it. The ambient `gh` login may be a different identity with no access to this repo
  # (→ "Could not resolve to a Repository"), so derive the slug from origin and reuse the token git
  # already has for that owner (via the configured credential helper) when it differs from gh's.
  REMOTE_URL="$(git -C "$REPO_ROOT" remote get-url origin 2>/dev/null || true)"
  [ -n "$REMOTE_URL" ] || die "no 'origin' remote found in $REPO_ROOT — can't determine the release repo."
  REPO_SLUG="$(printf '%s' "$REMOTE_URL" | sed -E 's#^(https://|git@)?github\.com[:/]+##; s#/*\.git$##; s#/+$##')"
  case "$REPO_SLUG" in
    */*) : ;;
    *) die "could not parse owner/repo from origin URL: $REMOTE_URL" ;;
  esac
  REPO_OWNER="${REPO_SLUG%%/*}"

  # If the active gh account can already see the repo, use it as-is; otherwise fall back to the
  # owner's git credential token. gh() runs gh with that token only when one is needed (an empty
  # GH_TOKEN would shadow gh's own keyring login).
  if gh repo view "$REPO_SLUG" >/dev/null 2>&1; then
    gh_pub() { gh "$@"; }
  else
    PUBLISH_TOKEN="$(printf 'protocol=https\nhost=github.com\nusername=%s\n' "$REPO_OWNER" \
                       | git -C "$REPO_ROOT" credential fill 2>/dev/null | sed -n 's/^password=//p')"
    [ -n "$PUBLISH_TOKEN" ] || die "gh account can't access $REPO_SLUG and no stored credential found for '$REPO_OWNER'. Run 'gh auth login' as that account or add its token."
    gh_pub() { GH_TOKEN="$PUBLISH_TOKEN" gh "$@"; }
  fi

  gh_pub release create "$PUBLISH_TAG" "$OUT_ZIP" "$OUT_MANIFEST" \
       --repo "$REPO_SLUG" --title "Aegis browser $VERSION" --notes "Chromium $VERSION" 2>/dev/null \
    || gh_pub release upload "$PUBLISH_TAG" "$OUT_ZIP" "$OUT_MANIFEST" \
         --repo "$REPO_SLUG" --clobber \
    || die "gh release publish to $REPO_SLUG failed."
  ok "Published to GitHub release $PUBLISH_TAG on $REPO_SLUG."
fi

printf '\n\033[1m✓ Browser published.\033[0m The guardian serves it at /dist/browser.zip + /dist/manifest.json.\n'
printf 'Kid Macs get it automatically when you run “Add a kid” in the console.\n'
