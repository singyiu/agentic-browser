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
| Browser dwell time | extension → `POST /dwell` → guardian | Prometheus `guardian_dwell_seconds_total{host, profile}` |
| Prize points | guardian grant / kid redeem | Prometheus `guardian_prize_points_balance{profile}` (gauge), `guardian_prize_points_changes_total{profile, direction}` |
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

## Per-profile screen time (embedded in the guardian dashboard)

The guardian dashboard (`:2947`) embeds two panels from this stack so a parent never has to leave
it. They are driven by the per-profile dwell metric `guardian_dwell_seconds_total{host, profile}`:

- **Panel 11 — "Screen time per day by profile"** (`timeseries`): `sum by (profile)
  (increase(guardian_dwell_seconds_total[1d]))`. Embedded on the dashboard's *Screen time* card.
- **Panel 12 — "Website time (selected range)"** (`table`): `topk(20, sum by (host)
  (increase(guardian_dwell_seconds_total{profile=~"$profile"}[$__range])))`. Embedded in the
  Activity *Screen time* tab, with the in-app selectors setting `var-profile` and a 48h `from`/`to`.

A `profile` dashboard variable (`label_values(guardian_dwell_seconds_total, profile)`) backs the
`$profile` filter. The guardian builds the embed URLs with Grafana's solo renderer, e.g.
`/d-solo/guardian-browser-usage/screen-time?panelId=12&var-profile=Hei&from=now-48h&to=now`.

**Embedding is enabled by `GF_SECURITY_ALLOW_EMBEDDING=true`** on the `lgtm` service (in
`docker-compose.yml`). Without it Grafana sends `X-Frame-Options: deny` and the iframes are refused.
Combined with the existing anonymous-Admin auth, the embedded panels render with no login — fine on
this loopback host (note the screen-time iframes are therefore not behind the guardian's parent PIN).

## Per-profile prize points (embedded in the guardian dashboard)

The guardian "Prize point" page embeds **Panel 13 — "Prize points per profile"** (`timeseries`):
`guardian_prize_points_balance{profile!="", profile!="global"}` over a 14-day range, one line per
kid. The balance is a gauge (re-seeded on guardian startup from each profile's stored balance), so
the line is continuous — it steps **up** on a parent grant and **down** on a kid redemption. The
companion counter `guardian_prize_points_changes_total{profile, direction}` (direction `grant` |
`redeem`) tracks change events. Same embedding + anonymous-Admin caveats as the screen-time panels
above.

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
