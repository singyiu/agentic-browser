#!/usr/bin/env bash
# Lay down the versioned GN build config and (re)generate the build directory.
# Run after sync.sh + apply-patches.sh and before build.sh. Keeps the RAM-safe
# build config (config/args.gn) reproducible across fresh checkouts and machines.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$HERE")"
SRC="$(cd "$REPO_ROOT/mac/src" && pwd)"
OUT="out/Release"

mkdir -p "$SRC/$OUT"
cp "$REPO_ROOT/config/args.gn" "$SRC/$OUT/args.gn"
echo "wrote $OUT/args.gn:"
cat "$REPO_ROOT/config/args.gn"
cd "$SRC"
gn gen "$OUT"
