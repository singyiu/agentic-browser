#!/usr/bin/env bash
# Optional hard safety net + monitor for the Chromium build on a low-RAM Mac.
#
# Run in a SECOND terminal while scripts/build.sh runs:
#     bash scripts/mem-watch.sh
#
# Prints macOS memory-pressure each tick so you can watch the build's footprint.
# If pressure stays CRITICAL (jetsam about to start killing apps) for KILL_AFTER
# consecutive ticks, it terminates the build to protect the host rather than
# letting it thrash into an unresponsive hang. Ctrl-C to stop watching.
#
# The concurrency caps in build.sh + args.gn should keep this from ever firing;
# it exists as insurance for the case the user asked about (host crashing on OOM).
set -euo pipefail

INTERVAL="${INTERVAL:-3}"        # seconds between checks
KILL_AFTER="${KILL_AFTER:-5}"    # consecutive CRITICAL ticks before killing
PATTERN='autoninja|siso|ninja'   # build process group to kill if needed
crit=0

# kern.memorystatus_vm_pressure_level: 1=normal, 2=warning, 4=critical
level_name() {
  case "$1" in
    1) echo normal ;;
    2) echo warning ;;
    4) echo CRITICAL ;;
    *) echo "?($1)" ;;
  esac
}

echo "watching memory pressure every ${INTERVAL}s (kill build after ${KILL_AFTER} CRITICAL ticks)"
while true; do
  lvl="$(sysctl -n kern.memorystatus_vm_pressure_level 2>/dev/null || echo 1)"
  freepct="$(memory_pressure 2>/dev/null | awk -F': ' '/free percentage/{print $2}')"
  printf '%s  pressure=%-8s free=%s\n' "$(date '+%H:%M:%S')" "$(level_name "$lvl")" "${freepct:-?}"

  if [ "${lvl:-1}" -ge 4 ]; then
    crit=$((crit + 1))
    if [ "$crit" -ge "$KILL_AFTER" ]; then
      echo "!! sustained CRITICAL memory pressure -> killing build to protect host"
      pkill -TERM -f "$PATTERN" 2>/dev/null || true
      sleep 2
      pkill -KILL -f "$PATTERN" 2>/dev/null || true
      exit 1
    fi
  else
    crit=0
  fi
  sleep "$INTERVAL"
done
