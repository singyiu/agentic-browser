#!/usr/bin/env bash
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$HERE")"
MAC_ROOT="$(cd "$REPO_ROOT/mac" && pwd)"
PIN="$(cat "$REPO_ROOT/PINNED_CHROMIUM")"

cd "$MAC_ROOT"
gclient sync --revision "src@${PIN}" --with_branch_heads --with_tags
