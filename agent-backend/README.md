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
uv sync                              # creates .venv + installs deps (runtime + dev) from uv.lock
uv run playwright install chromium   # for the integration tests

claude setup-token                   # uses your Claude Max subscription
cp .env.example .env                 # paste the token into CLAUDE_CODE_OAUTH_TOKEN
```

> Uses [uv](https://docs.astral.sh/uv/) (`brew install uv`); `uv.lock` pins every dependency for
> reproducible installs. Without uv: `python3 -m venv .venv && source .venv/bin/activate && pip
> install -e . --group dev`.

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
uv run pytest                 # unit + integration (integration launches its own headless Chromium)
uv run pytest -m "not integration"   # unit only (fast)
uv run pytest --cov=agent_backend --cov-report=term-missing
uv run ruff check src tests && uv run black --check src tests && uv run mypy src
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

- **First-time setup:** the first time you open `http://127.0.0.1:2947/setup` (or `/review`,
  which redirects there until a PIN exists), a wizard walks you through choosing a 4–8 digit
  parent PIN. It is saved as a salted hash under `data/guardian_admin.json` and applies
  immediately — no restart, no `.env` editing, never stored in plaintext. (Setting
  `GUARDIAN_PARENT_PIN` in `.env` still works and skips the wizard.) Forgot it? Delete that file
  and re-run the wizard.
- **Parent review UI:** open `http://127.0.0.1:2947/review` in any browser and enter your PIN.
  Pending requests show the URL, why it was blocked, the kid's note, and an **editable "allow"
  field pre-filled with the URL** — broaden it to a section (`youtube.com/results*`) or a topic
  (`BeyBlade anime`) before approving, or reject with an optional note.
- **The kid gets unblocked** by tapping **Check if approved** on the block page; once
  approved it reopens the page (the backend allows it immediately and the extension's cached
  block is evicted).
- **Security:** the parent PIN is stored only as a salted hash (`data/guardian_admin.json`) or in
  `.env`, and is **never** written into `extension/guardian-config.json`. The kid's browser holds
  `GUARDIAN_TOKEN` and can reach `127.0.0.1:2947`, so submitting/checking a request uses the token,
  but **approval requires the PIN the kid's browser never receives**. With no PIN configured the
  review endpoints return `503` and the UI sends the parent to `/setup`. Requests are stored in
  `data/guardian_requests.json` (gitignored). On a LAN deployment, finish `/setup` before exposing
  the port — once a PIN exists the setup endpoint is closed (`409`).

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

## Running the browser and guardian on different computers (LAN)

By default everything runs on one machine (`localhost`). You can instead run the **guardian on the
parent's computer** and **Chromium + the extension on the kid's computer** on the same LAN — so the
classifier, `/review`, the whitelist, and the parent PIN all live on the parent's machine, out of
the kid's reach. Only the extension→guardian HTTP crosses the network (the browser-automation CDP
stays on the browser machine and is not exposed).

**On the parent (guardian) machine** — in `agent-backend/.env`:
- `GUARDIAN_TOKEN=<long random string>` and `GUARDIAN_PARENT_PIN=<pin>`
- `GUARDIAN_HOST=0.0.0.0` (accept LAN connections)

Then run `scripts/launch-guardian.sh` and note this machine's LAN IP (e.g. `192.168.1.50`). To
keep the guardian up across reboots and crashes, install it as a background service — see **Run the
guardian as an always-on service** below.

**On the kid (browser) machine** — in `agent-backend/.env`:
- `GUARDIAN_TOKEN=<same value as the parent machine>`
- `GUARDIAN_ENDPOINT=http://192.168.1.50:2947` (the parent's IP + `GUARDIAN_PORT`)

Then run `scripts/launch-chromium.sh` — it writes that endpoint into
`extension/guardian-config.json` and prints `Guardian endpoint: …` so you can confirm. Do **not**
set `GUARDIAN_PARENT_PIN` on this machine.

Leave both `GUARDIAN_HOST` and `GUARDIAN_ENDPOINT` at their defaults for the single-machine
(`localhost`) setup — unchanged.

**Security on a LAN:**
- `GUARDIAN_TOKEN` now protects the guardian against the whole LAN — use a long random value and
  keep it identical on both machines.
- `GUARDIAN_PARENT_PIN` stays only on the parent's machine and is never sent to the kid's browser,
  so the approval secret isn't on the kid's computer at all.
- `extension/guardian-config.json` holds the token and is readable by any page the kid visits (it's
  a web-accessible extension resource), so anything on the LAN that learns the token could call the
  guardian. Scope the firewall to **allow only the kid's machine** to reach ports `2947` (and metrics
  `2948`), block everything else, and rotate `GUARDIAN_TOKEN` if it leaks.
- **Firewall must permit the inbound connection.** Setting `GUARDIAN_HOST=0.0.0.0` binds all
  interfaces, but the OS firewall still has to allow incoming traffic to `GUARDIAN_PORT`. On macOS,
  if the application firewall is set to "block all incoming connections" (and/or stealth mode), it
  silently drops the connection from the kid's machine — add an allow rule for the guardian (or the
  Python/uvicorn binary) on the parent's machine. Quick check from the kid's machine:
  `python3 -c "import urllib.request; print(urllib.request.urlopen('http://<parent-ip>:2947/health').read())"`.

## Multiple teen profiles (LAN)

One guardian can govern **several teens at once**, each on their own computer, with **separate rules
per teen** — a site approved for one teen stays blocked for the others, and one teen can't see
another's requests. Each teen's browser uses its **own** `GUARDIAN_TOKEN`; the guardian maps the
token to that teen's profile.

**On the parent (guardian) machine** — list the teens in `data/guardian_profiles.json` (a JSON
array; copy `guardian_profiles.example.json` to start), each with a name and a long random token:

```json
[
  { "name": "alex", "token": "<long-random-secret-for-alex>" },
  { "name": "sam",  "token": "<long-random-secret-for-sam>" }
]
```

Set `GUARDIAN_HOST=0.0.0.0` and `GUARDIAN_PARENT_PIN=<pin>` in `.env`, then run
`scripts/launch-guardian.sh` (it prints `Teen profiles: alex, sam`). Each teen gets isolated files
under `data/profiles/<name>/` (whitelist, requests, cache), created automatically. `GUARDIAN_TOKEN`
is **not** needed when a profiles file is present. Optional per-teen overrides
`whitelist_path` / `requests_path` / `cache_path` may be added to a profile entry.

**On each teen's (browser) machine** — in `agent-backend/.env` set that teen's own token plus the
parent's endpoint, then run `scripts/launch-chromium.sh` (unchanged from single-machine):
- `GUARDIAN_TOKEN=<that teen's token from the registry>`
- `GUARDIAN_ENDPOINT=http://<parent-ip>:2947`

**Reviewing:** open `http://<parent-ip>:2947/review` on the parent's machine and enter the PIN.
Every teen's pending and recent requests appear on one page, each **labelled with the teen's name**;
approving adds the entry to **that teen's** whitelist only.

**Notes & security:**
- Give each teen a **different** long random token; a teen who learns another's token could act as
  them. The registry file holds every teen's token, so keep it only on the parent's machine (never
  copy it to a teen's computer — each teen's `.env` holds just their own token) and restrict its
  file permissions (e.g. `chmod 600`).
- Profiles are read once at startup, so **adding or removing a teen needs a guardian restart**.
- With no `data/guardian_profiles.json` (or an empty list), the guardian runs exactly as before: a
  single profile using `GUARDIAN_TOKEN` and the legacy `data/guardian_whitelist.json` /
  `data/guardian_requests.json` — single-machine and single-teen-LAN setups are unchanged.
- The LAN firewall guidance above still applies: allow only the teens' machines to reach `2947`.

## Run the guardian as an always-on service

The guardian should stay up whenever the kid's browser might be used — a manual
`bash scripts/launch-guardian.sh` in a terminal dies on logout, reboot, or crash. Install it as a
background service that **starts at login and restarts automatically**:

```sh
bash scripts/install-guardian-service.sh     # macOS (launchd) or Linux (systemd --user)
```

It reuses `launch-guardian.sh`, reads your existing `.env`, and then probes `/health` to confirm the
guardian actually came up (printing the tail of `data/guardian.err.log` if it didn't). Process output
goes to `data/guardian.out.log` and `data/guardian.err.log`.

```sh
# macOS — status / logs / remove:
launchctl print "gui/$(id -u)/com.agentic-browser.guardian" | grep -iE 'state|pid'
tail -f data/guardian.out.log data/guardian.err.log
bash scripts/uninstall-guardian-service.sh
```

- **No secrets in the service file.** The generated unit (`~/Library/LaunchAgents/` on macOS,
  `~/.config/systemd/user/` on Linux) holds only paths; the guardian still loads
  `CLAUDE_CODE_OAUTH_TOKEN` / `GUARDIAN_TOKEN` / `GUARDIAN_PARENT_PIN` from `.env` at startup.
- **`claude` CLI / `node` must be on `PATH`.** The classifier spawns the Claude Code CLI, so the
  installer bakes your current `PATH` into the unit (launchd/systemd otherwise start with a minimal
  `PATH` that misses nvm/npm bins). If you switch Node versions (e.g. via nvm), **re-run the
  installer** so the baked path stays valid.
- **Restart after editing profiles.** Profiles are read once at startup, so after changing
  `data/guardian_profiles.json` restart the service — macOS:
  `launchctl kickstart -k "gui/$(id -u)/com.agentic-browser.guardian"`; Linux:
  `systemctl --user restart agentic-guardian.service`.
- **Linux parent boxes.** The same installer writes a systemd `--user` unit from
  `deploy/guardian.systemd.service.template`; run `loginctl enable-linger "$USER"` to keep it running
  without an active login. (Provided for the LAN topology; verified on macOS.)

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
