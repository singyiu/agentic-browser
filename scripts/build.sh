#!/usr/bin/env bash
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$HERE")"
SRC="$(cd "$REPO_ROOT/mac/src" && pwd)"

# Ensure the RAM-safe GN config is in place before building. A fresh checkout
# has no out/Release/args.gn, and Chromium's defaults overcommit memory on this
# 18 GB machine (see config/args.gn). Don't build with the crash-prone defaults.
if [ ! -f "$SRC/out/Release/args.gn" ]; then
  bash "$HERE/setup-gn.sh"
fi

cd "$SRC"

# Cap concurrent compile jobs so peak RAM stays under this 18 GB machine's
# physical memory. Chromium's heavy translation units use 1-2 GB each; siso's
# unbounded default (~12 on a 12-core CPU, with no memory throttle) thrashes
# swap and hangs the host. Override with BUILD_JOBS=N for different hardware.
JOBS="${BUILD_JOBS:-6}"
autoninja -C out/Release -j "$JOBS" chrome
