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
