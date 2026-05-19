#!/usr/bin/env bash
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FORK_ROOT="$(dirname "$HERE")"
SRC="$(cd "$FORK_ROOT/../mac/src" && pwd)"

git -C "$SRC" reset --hard
git -C "$SRC" clean -fdx -e out/
