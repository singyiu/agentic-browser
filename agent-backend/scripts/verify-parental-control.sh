#!/usr/bin/env bash
# Manual end-to-end smoke test. Start the guardian first (scripts/launch-guardian.sh),
# then run this with GUARDIAN_TOKEN set to the same value.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_ROOT="$(dirname "$HERE")"
PY="$BACKEND_ROOT/.venv/bin/python"
ENDPOINT="${GUARDIAN_ENDPOINT:-http://127.0.0.1:2947}"
: "${GUARDIAN_TOKEN:?set GUARDIAN_TOKEN to the same value the guardian service uses}"

"$PY" - "$ENDPOINT" "$GUARDIAN_TOKEN" <<'PYEOF'
import sys
import httpx

endpoint, token = sys.argv[1], sys.argv[2]
headers = {"X-Guardian-Token": token}
print("health:", httpx.get(endpoint + "/health", timeout=5).json())


def classify(name, payload):
    resp = httpx.post(endpoint + "/classify", json=payload, headers=headers, timeout=40)
    body = resp.json()
    print(f"{name}: verdict={body.get('verdict')} reason={body.get('reason', '')[:70]!r}")


classify("safe", {
    "url": "https://en.wikipedia.org/wiki/Cat",
    "title": "Cat - Wikipedia",
    "body_snippet": "The cat is a small domesticated carnivorous mammal...",
})
classify("youtube", {
    "url": "https://www.youtube.com/watch?v=demo",
    "title": "Nursery rhymes for kids",
    "body_snippet": "Sing along nursery rhymes for children.",
})
PYEOF

echo
echo "Now run: bash scripts/launch-chromium.sh  and browse to a test page."
echo "Blocked events are logged to: $BACKEND_ROOT/data/guardian_events.jsonl"
