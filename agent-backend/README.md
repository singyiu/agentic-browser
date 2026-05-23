# agent-backend

Claude-Max-backed agentic browser-control backend for the `agentic-browser` fork.

It runs an autonomous loop with Claude (your **Claude Max** subscription, via the
Claude Agent SDK) that drives a Chromium tab through a **browser-control MCP server**.
Claude's config/skills are isolated from your personal `~/.claude` via `CLAUDE_CONFIG_DIR`.

## How it fits together

```
runner (Agent SDK, query())  ──spawns──▶  browser-control MCP server (stdio)
        │                                          │ Playwright connect_over_cdp
        │ CLAUDE_CODE_OAUTH_TOKEN (Max)            ▼
        └── CLAUDE_CONFIG_DIR=claude-config   Chromium  (--remote-debugging-port)
```

- `src/agent_backend/browser/` — `BrowserController` (Playwright/CDP) + pure helpers.
- `src/agent_backend/mcp_server/` — FastMCP stdio server exposing `browser_*` tools.
- `src/agent_backend/runner/` — Agent SDK runner (subscription auth, browser-only tools).
- `claude-config/` — the isolated `CLAUDE_CONFIG_DIR` (settings, `.mcp.json`, `CLAUDE.md`).

## Setup

```sh
cd agent-backend
python3 -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
playwright install chromium          # for the integration tests

claude setup-token                   # uses your Claude Max subscription
cp .env.example .env                 # paste the token into CLAUDE_CODE_OAUTH_TOKEN
```

> Do **not** set `ANTHROPIC_API_KEY` — it overrides the subscription and bills per-token.
> The runner refuses to start if it is set.

## Run

```sh
# Terminal 1 — the browser the agent will drive (your built Chromium):
bash scripts/launch-chromium.sh

# Terminal 2 — give the agent a task:
bash scripts/run-agent.sh "Open https://example.com, report the H1, then click 'More information'"
```

`run-agent.sh` sets `CLAUDE_CONFIG_DIR=claude-config`, so all Claude state stays
inside this folder and never touches your personal `~/.claude`.

## Test

```sh
pytest                 # unit + integration (integration launches its own headless Chromium)
pytest -m "not integration"   # unit only (fast)
pytest --cov=agent_backend --cov-report=term-missing
ruff check src tests && black --check src tests && mypy src
```

## Parental whitelist (guardian)

The guardian pre-approves content via `data/guardian_whitelist.json` (a JSON array of
strings; override the path with `GUARDIAN_WHITELIST_PATH`). Each entry's behavior is
auto-detected from its shape:

| Entry | Type | Effect |
|-------|------|--------|
| `www.youtube.com` | exact URL | that page is allowed instantly (classifier skipped) |
| `www.youtube.com/results*` | wildcard URL (`*`) | matching pages allowed instantly |
| `BeyBlade anime` | content (natural language) | the classifier is told the topic is parent-approved |

- **URL rules are authoritative**: a match returns `allow` *before* the cache and without an
  LLM call. `*` placement controls breadth — `youtube.com*` allows the whole site (incl.
  videos), `youtube.com/*` allows sub-paths only, `www.youtube.com` allows just that page.
- **Content rules are best-effort**: they steer the model toward `allow`, but the
  always-block categories (adult, graphic violence, self-harm, hate, illegal/dangerous)
  still block.
- A missing/invalid file means an empty whitelist — everything is classified normally
  (fails safe). Edits take effect on the next request and clear stale cached verdicts.

Worked example — let a kid browse/search YouTube while videos stay classified:
add `www.youtube.com` **and** `www.youtube.com/results*`; individual `…/watch?v=…` pages
match no rule, so they are still classified (and a `BeyBlade anime` content entry then lets
the matching videos through).

Manage it over HTTP (token-authed, like `/classify`; `$TOKEN` = `GUARDIAN_TOKEN`):

```sh
curl -s -H "X-Guardian-Token: $TOKEN" http://127.0.0.1:2947/whitelist            # list
curl -s -X POST -H "X-Guardian-Token: $TOKEN" -H 'Content-Type: application/json' \
  -d '{"entry":"www.youtube.com"}' http://127.0.0.1:2947/whitelist               # add
curl -s -X DELETE -H "X-Guardian-Token: $TOKEN" -H 'Content-Type: application/json' \
  -d '{"entry":"www.youtube.com"}' http://127.0.0.1:2947/whitelist               # remove
```

## Request access (guardian)

When a page is blocked, the kid can ask for it from the block page (a **Request access**
button with an optional note). The parent reviews the request and approves or rejects it;
**approving adds the chosen entry to the whitelist**, so the page then follows the whitelist
rules above.

- **Parent review UI:** open `http://127.0.0.1:2947/review` in any browser and enter
  `GUARDIAN_PARENT_PIN`. Pending requests show the URL, why it was blocked, the kid's note,
  and an **editable "allow" field pre-filled with the URL** — broaden it to a section
  (`youtube.com/results*`) or a topic (`BeyBlade anime`) before approving, or reject with an
  optional note.
- **The kid gets unblocked** by tapping **Check if approved** on the block page; once
  approved it reopens the page (the backend allows it immediately and the extension's cached
  block is evicted).
- **Security:** `GUARDIAN_PARENT_PIN` lives only in `.env` and is **never** written into
  `extension/guardian-config.json`. The kid's browser holds `GUARDIAN_TOKEN` and can reach
  `127.0.0.1:2947`, so submitting/checking a request uses the token, but **approval requires
  the PIN the kid's browser never receives**. With no PIN set, the review endpoints return
  `503` (feature disabled). Requests are stored in `data/guardian_requests.json` (gitignored).

```sh
# Kid side (token): submit a request, then poll its status.
curl -s -X POST -H "X-Guardian-Token: $TOKEN" -H 'Content-Type: application/json' \
  -d '{"url":"https://www.youtube.com/watch?v=abc","note":"for homework"}' \
  http://127.0.0.1:2947/access-request
curl -s -H "X-Guardian-Token: $TOKEN" \
  "http://127.0.0.1:2947/access-request?url=https://www.youtube.com/watch?v=abc"

# Parent side (PIN = GUARDIAN_PARENT_PIN): list pending, then approve/reject.
curl -s -H "X-Guardian-Parent-Pin: $PIN" http://127.0.0.1:2947/review/requests
curl -s -X POST -H "X-Guardian-Parent-Pin: $PIN" -H 'Content-Type: application/json' \
  -d '{"id":"req_…","decision":"approve","whitelist_entry":"BeyBlade anime"}' \
  http://127.0.0.1:2947/review/decision
```

## MCP tools

`browser_navigate`, `browser_snapshot` (accessibility tree), `browser_click`,
`browser_type`, `browser_read`, `browser_wait_for`, `browser_back`, `browser_screenshot`.
Elements are targeted by ARIA role+name, CSS selector, or visible text.

## Notes & limits

- Personal-use posture: driving Claude Code with your own subscription via
  `CLAUDE_CODE_OAUTH_TOKEN` is the documented path. Distributing this to other users
  on their own subscriptions is **not** permitted — switch to `ANTHROPIC_API_KEY` then
  (only `config.py`/the runner env need to change).
- From 2026-06-15, Agent SDK / `claude -p` usage draws on a separate monthly credit;
  validate against your expected autonomous-action volume.
- Deferred: ref-based snapshot interaction, multi-tab, and the in-Chromium UI entry point
  (this backend currently drives Chromium externally over CDP).
