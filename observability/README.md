# Observability stack (Grafana LGTM + Alloy)

Local Grafana **LGTM** (Loki · Grafana · Tempo · Mimir/Prometheus, via the all-in-one
`grafana/otel-lgtm` image) plus **Grafana Alloy**, for monitoring the agentic browser.

The agentic backend (guardian) runs on the **host**, not in a container. Alloy reaches it
over `host.docker.internal` and reads host log files through read-only bind mounts.

## What's collected

| Signal | Source | Where |
|--------|--------|-------|
| Classification metrics | host guardian `/metrics` (`:2948`), scraped by Alloy | Prometheus `guardian_*` |
| Guardian events | `agent-backend/data/guardian_events.jsonl` | Loki `{job="guardian"}` |
| Chromium process logs | `.chromium-profile/chrome_debug.log` | Loki `{job="chromium"}` |
| Browser dwell time | extension → `POST /dwell` → guardian | Prometheus `guardian_dwell_seconds_total` |
| Claude Code transcripts | `~/.claude/projects/.../*.jsonl` (opt-in, **redacted**) | Loki `{job="claude_transcript"}` |

## Quick start

```bash
cd observability
cp .env.example .env          # adjust REPO_ROOT / ports if needed
docker compose up -d
```

Prerequisites: the guardian + Chromium must be running on the host so there are metrics/logs
to collect:

```bash
cd ../agent-backend
scripts/launch-guardian.sh    # API :2947, Prometheus metrics :2948
scripts/launch-chromium.sh    # built Chromium + extension + chrome_debug.log
```

Then open **http://localhost:3000** (anonymous admin) → dashboard **“Guardian — Browser Usage”**.

## Enabling Claude transcript ingestion (opt-in)

Disabled by default (an empty dir is mounted). To ship transcripts to Loki — secrets are
redacted by Alloy first, but they can still contain PII — set in `.env`:

```bash
TRANSCRIPTS_DIR=/Users/<you>/.claude/projects/-Users-<you>-sing-agentic-browser
docker compose up -d
```

Redaction (Alloy `loki.process`) masks `sk-ant-*` keys, `Bearer` tokens, AWS access keys,
long hex (incl. `GUARDIAN_TOKEN`), and `password|secret|token|credential|api_key` values.
Tail-only: existing transcript history is not back-filled unless the `alloy-data` volume is
removed.

## Query cheat-sheet

PromQL:
- Block rate: `sum(rate(guardian_classifications_total{verdict="block"}[15m]))`
- Classify p95 (ms): `histogram_quantile(0.95, sum(rate(guardian_classification_duration_ms_bucket[10m])) by (le))`
- Top domains: `topk(10, sum by (host) (rate(guardian_visits_total[1h])))`
- Dwell by host: `topk(10, sum by (host) (rate(guardian_dwell_seconds_total[1h])))`
- Cache hit ratio: `sum(rate(guardian_cache_hits_total[1h])) / sum(rate(guardian_visits_total[1h]))`
- Whitelist allows by host: `topk(10, sum by (host) (rate(guardian_whitelist_hits_total[1h])))`

LogQL:
- Blocks: `{job="guardian", event="block"} | json`
- Whitelist allows: `{job="guardian", event="whitelist_allow"} | json`
- Errors in Chromium: `{job="chromium"} |~ "(?i)error"`

## Gotchas

- **macOS only** for `host.docker.internal` (Docker Desktop). The guardian metrics server binds
  `0.0.0.0` so the container can reach it — fine on a personal machine; it exposes only
  aggregate counts + domain names, no secrets.
- **Persistence**: metrics/logs live in the `lgtm-data` volume. `docker compose down` keeps it;
  `docker compose down -v` deletes all stored data.
- **Extension changes**: bump `extension/manifest.json` `version` so Chromium refreshes the
  cached service worker (otherwise dwell/blocking changes won't load).
- Image tags are pinned (`otel-lgtm:0.11.6`, `alloy:v1.10.0`) for reproducibility.
