#!/usr/bin/env bash
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FORK_ROOT="$(dirname "$HERE")"
SRC="$(cd "$FORK_ROOT/../mac/src" && pwd)"

shopt -s nullglob

# 1. Apply *.patch files in sorted order.
for p in "$FORK_ROOT"/patches/*.patch; do
  echo "applying $(basename "$p")"
  git -C "$SRC" apply --3way "$p"
done

# 2. Copy overrides/<path> to src/<path>.
if [ -d "$FORK_ROOT/overrides" ]; then
  cd "$FORK_ROOT/overrides"
  find . -type f ! -name '.gitkeep' -print0 | while IFS= read -r -d '' f; do
    rel="${f#./}"
    dest="$SRC/$rel"
    mkdir -p "$(dirname "$dest")"
    cp "$f" "$dest"
    echo "override $rel"
  done
fi
