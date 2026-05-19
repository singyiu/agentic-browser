#!/usr/bin/env bash
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FORK_ROOT="$(dirname "$HERE")"
MAC_ROOT="$(cd "$FORK_ROOT/../mac" && pwd)"
PIN="$(cat "$FORK_ROOT/PINNED_CHROMIUM")"

cd "$MAC_ROOT"
gclient sync --revision "src@${PIN}" --with_branch_heads --with_tags
