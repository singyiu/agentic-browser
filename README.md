# agentic-browser

Overlay-style Chromium fork. The upstream checkout in `mac/src` is kept pristine;
our customizations live here as patches, file overrides, and build config that are
applied on top of `mac/src` before each build.

`mac/` (the full Chromium checkout, ~100 GB, gclient-managed) is **git-ignored** —
it is reproducible from `PINNED_CHROMIUM` via `gclient sync`, so it is never
committed to this repo.

## Layout

- `PINNED_CHROMIUM` — upstream commit SHA the patches/overrides target.
- `patches/` — `*.patch` files, applied in sorted order with `git apply --3way`.
- `overrides/` — files mirroring `mac/src/` paths; copied over `mac/src/` 1:1.
- `config/args.gn` — GN build args (RAM-bounded for an 18 GB machine; see below).
- `scripts/` — sync / apply-patches / setup-gn / build / reset / mem-watch helpers.
- `mac/` — the Chromium checkout (git-ignored, not part of this repo).
- `agent-backend/` — **Aegis**, the Claude-Max agentic browser-control backend with a parental guardian (Python; see `agent-backend/README.md`).

## Workflow

```sh
bash scripts/reset-src.sh        # undo any prior overlay in mac/src
bash scripts/sync.sh             # gclient sync to PINNED_CHROMIUM
bash scripts/apply-patches.sh    # lay our changes over mac/src
bash scripts/setup-gn.sh         # write out/Release/args.gn + gn gen
bash scripts/build.sh            # RAM-bounded build (autoninja -j6); BUILD_JOBS=N to override
```

`build.sh` runs `setup-gn.sh` automatically if `out/Release/args.gn` is missing,
so you can't accidentally build with Chromium's memory-hungry defaults.

## Build config & memory

This repo targets an 18 GB Apple-silicon Mac, which cannot run Chromium's default
build parallelism without exhausting RAM and hanging the host. `config/args.gn`
pins a RAM-safe configuration:

- `is_component_build = true` — many small `.dylib` links instead of one giant
  static link, so no single link spikes memory.
- `concurrent_links = 1` — never overlap heavy link steps.
- `build.sh` caps compile jobs at `-j 6` (override with `BUILD_JOBS=N`).
- `scripts/mem-watch.sh` — optional watchdog; run it in a second terminal to
  monitor memory pressure and kill the build before it can OOM the host.

## Capturing a new patch

After hacking on `mac/src`:

```sh
git -C mac/src diff > patches/NNN-short-name.patch
```

For brand-new files or whole-file replacements, drop them into
`overrides/<same-path-as-in-mac/src>`.

## Bumping Chromium

1. Pick a new upstream SHA.
2. `echo <sha> > PINNED_CHROMIUM`
3. `bash scripts/reset-src.sh && bash scripts/sync.sh`
4. `bash scripts/apply-patches.sh` — resolve any 3-way conflicts.
5. Re-export resolved patches as in "Capturing a new patch".
6. `bash scripts/setup-gn.sh && bash scripts/build.sh`.

## Constraints

- Never commit changes inside `mac/src` — that checkout always reflects upstream.
- Do not push `mac/src` to any remote.
- `mac/` is git-ignored; the Chromium source is reproduced via `PINNED_CHROMIUM`.

## Security

Threat model, what the hardening pass fixed, and accepted residual risks for the
Aegis guardian/extension stack. Read this before changing anything that touches
tokens, enrollment, or kid-facing endpoints.

### Threat model

- **Deployment**: one guardian service on the parent's Mac; kid Macs on the same
  home LAN run a locked Chromium with a force-installed extension.
- **Adversary**: a motivated, technical kid with full access to their own Mac
  (DevTools, their own terminal) — not a network nation-state. LAN neighbors
  (guests, compromised IoT) are a secondary, opportunistic adversary.
- **Non-goals**: surviving root/admin compromise of either Mac; hiding from the
  kid that monitoring exists (the system is deliberately overt).
- **Crown jewels**: the parent PIN (full control), per-kid bearer tokens
  (kid-level API access), the extension signing key
  (`agent-backend/.secrets/aegis-ext.pem` — controls what the kid browser runs),
  and the parent's Claude OAuth token. All gitignored; never logged.

### Fixed in the June 2026 hardening pass

- **Token theft from any web page**: `guardian-config.json` (bearer token +
  endpoint) was in the extension's `web_accessible_resources` for `<all_urls>` —
  one `fetch()` from any page's console stole it. Removed from WAR.
- **Global-blocklist bypass**: `/whitelist` POST/DELETE accepted the kid's own
  token, and per-kid allow entries deliberately outrank the Global blocklist
  ("individual wins"). Mutations now also require the parent PIN.
- **First-run PIN race**: before a PIN existed, the first `POST /setup/pin` from
  anywhere on the LAN became the parent. PIN creation is now loopback-only, and
  pre-PIN `/setup/health` no longer reveals LAN IP/model/profiles to non-local
  callers.
- **Rule files lost on crash**: six stores wrote JSON non-atomically; a crash
  mid-write truncated the file and the next load silently reset the parent's
  rules. All stores now write temp-file + `os.replace` (`guardian/fsio.py`).
- **Dwell drain on the block page**: blocking a tab left the dwell clock running
  on the blocked URL, so sitting on the block page kept draining the budget. The
  clock is now banked and stopped on every block.
- **Forged screen-time reports**: `/dwell` accepted unbounded `dwell_ms`; capped
  at 6h per report.
- **Navigation/submit races**: per-tab sequence guards stop SPA double-fires
  (duplicate LLM calls, stale verdicts blocking the wrong page); search/chat
  submissions share one in-flight check per query (no double-submit).
- **Enrollment integrity**: the AirDropped kid bootstrap pins the SHA256 of the
  updater script it later downloads over LAN HTTP, validates substituted values,
  and guards the manifest app name; the CRX config is emitted with real JSON
  serialization via env (no token in `ps`).
- **Headers & scripts**: `nosniff` everywhere + `frame-ancestors 'self'` on HTML;
  installers run `set -e`, XML-escape launchd rendering, keep a user-set
  `GUARDIAN_HOST`, pin the uv bootstrap curl to https/TLS≥1.2, and guard
  `rm -rf` targets. Classifier failure verdict is configurable
  (`GUARDIAN_CLASSIFY_FAIL_MODE=open|closed`, default `open`).

### Accepted residual risks and roadmap

- **Plain-HTTP transport on the LAN** — the extension sends its token, page
  snippets, queries, and escalation screenshots over HTTP; sniffable on shared
  Wi-Fi. *Roadmap*: guardian CA installed on kid Macs at enrollment (bootstrap
  already has sudo), HTTPS listener + update_url, then drop
  `ExtensionAllowInsecureUpdates` (required while updates ride HTTP; CRX3
  signatures pinned to the extension ID anchor update integrity meanwhile).
- **Managed-preferences fragility (bites in practice)** — macOS periodically
  rebuilds `/Library/Managed Preferences/`, wiping hand-placed plists; this
  deleted the local policy on 2026-06-09 and will eventually hit kid Macs.
  Symptom: the extension stops force-installing (a cached install keeps running
  until the profile is scrubbed). Fix today: re-run
  `sudo bash agent-backend/scripts/install-extension-policy.sh` (or the kid
  bootstrap). *Roadmap*: ship the policy as a `.mobileconfig` configuration
  profile; make `kid-update-check.sh` detect the missing plist and alert.
- **Iframe gap** — content scripts run `all_frames: false` and subframe
  navigations are ignored, so blocked content inside an `<iframe>` on an allowed
  page is invisible. *Roadmap*: sync hard blocklist hosts into
  `declarativeNetRequest` (also a synchronous pre-render blocking layer).
- **Fail-open windows** — pages stay visible during classification (up to the
  185s budget; doubled on screenshot escalation); classifier errors allow unless
  fail mode is `closed`; the in-memory verdict cache dies with the MV3 worker.
  The server-side time budget (8s, runs first) is the hard backstop. *Roadmap*:
  persist the verdict cache to `chrome.storage.session`; DNR for known-blocked
  hosts.
- **Offline data loss** — dwell reports are best-effort; guardian-unreachable
  segments are dropped (and enforcement fails open while it is down). *Roadmap*:
  queue unsent segments in `chrome.storage.session`; surface unreachable streaks
  on the dashboard.
- **Token rotation** — requires repacking + redelivering the CRX; no live
  propagation. *Roadmap*: short-lived tokens or a `/config` poll with grace.
- **Smaller items** — `localhost` is hard-allowed (kid-run local servers are
  unmonitored); unknown hosts treat `?q=` as a search (protective but can
  false-positive); Prometheus `host`/`categories` labels are unbounded; Grafana
  base URL hardcoded in `static/shell.js`; naive eTLD+1 in `normalize.py`
  (metrics/digest only); the parent installer pipes Astral's uv installer to
  `sh` (accepted, now https-pinned).

Report issues via a GitHub issue (no real tokens/PINs in reports, please).
