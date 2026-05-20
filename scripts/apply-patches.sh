#!/usr/bin/env bash
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$HERE")"
SRC="$(cd "$REPO_ROOT/mac/src" && pwd)"

shopt -s nullglob

# 1. Apply *.patch files in sorted order.
for p in "$REPO_ROOT"/patches/*.patch; do
  echo "applying $(basename "$p")"
  git -C "$SRC" apply --3way "$p"
done

# 2. Copy overrides/<path> to src/<path>.
if [ -d "$REPO_ROOT/overrides" ]; then
  cd "$REPO_ROOT/overrides"
  find . -type f ! -name '.gitkeep' -print0 | while IFS= read -r -d '' f; do
    rel="${f#./}"
    dest="$SRC/$rel"
    mkdir -p "$(dirname "$dest")"
    cp "$f" "$dest"
    echo "override $rel"
  done
fi
