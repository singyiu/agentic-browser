# Set up the kid's browser on a Mac (LAN, codex guardian on Linux)

> **Update 2026-07-04 — the automated locked-browser installer now works from this guardian.**
> The guardian serves a complete Chromium.app (Apple Silicon, macOS ≥ 13) from its dist dir and
> packs a per-kid force-install CRX on this Linux box. On the kid Mac just download and run
> `http://192.168.50.206:2947/enroll/<kid>` (the "Set up `<kid>`.command" file — also offered by
> the dashboard's *Add a kid* flow). It installs the browser to /Applications (asks for the Mac
> password), applies the tamper-lock policy, and sets up hourly extension updates.
> The manual, **not tamper-locked** path below still works as a fallback (e.g. Intel Macs).

This guardian runs on a **Linux** host (`http://192.168.50.206:2947`, codex / `gpt-5.5`).

Manual fallback: the kid's Mac uses **stock Google Chrome** with the parental-control **extension
loaded unpacked**, pointed at the guardian. This is fully functional immediately (every page is
classified, blocks show the Aegis block page, screen-time + prize points work). It is **not
tamper-locked** — the kid can disable it at `chrome://extensions`. See *Optional: lock it down* at
the end to harden it.

> Replace `192.168.50.206` if the guardian's LAN IP changes (give it a DHCP reservation / static IP
> to avoid that). Do every step on the machine named in the heading.

---

## Prerequisites

- The kid's Mac and the guardian (Linux box, `192.168.50.206`) are on the **same LAN / Wi-Fi**.
- **Google Chrome** installed on the Mac (Chrome ≥ 114; Chromium or Edge also work).
- The parent PIN is set on the guardian (already done).

---

## Part A — Create the kid + copy the token  (on the guardian dashboard)

1. Open the dashboard and sign in with the parent PIN:
   - from the guardian host: `http://127.0.0.1:2947/`
   - or from any LAN machine: `http://192.168.50.206:2947/`
2. **Profiles → Create**, type the kid's name (letters/digits/`-`/`_`), click create.
3. The reveal panel shows the kid's **Token**, a ready-made **`guardian-config.json`**, and a launch
   command. **Copy the token now — it is shown only once.** (If you lose it: Profiles → that kid →
   **Regenerate token**.)

The config you'll need on the Mac is exactly:

```json
{ "token": "PASTE_THE_KID_TOKEN_HERE", "endpoint": "http://192.168.50.206:2947" }
```

---

## Part B — Open the guardian's port to the kid Mac  (on the Linux guardian)

`ufw` is active, so the Mac can't reach the guardian until you allow it. Find the **kid Mac's LAN
IP** (Mac: System Settings → Wi-Fi → **Details…** → IP Address, e.g. `192.168.50.x`), then on the
Linux box:

```bash
sudo ufw allow from <KID_MAC_IP> to any port 2947 proto tcp   # guardian API (required)
sudo ufw allow from <KID_MAC_IP> to any port 2948 proto tcp   # Prometheus metrics (optional)
```

Scoping to the kid's IP is safer than opening the port to the whole LAN. (Whole-LAN, if you prefer:
`sudo ufw allow 2947/tcp`.)

---

## Part C — Confirm the Mac can reach the guardian  (on the kid Mac)

Open **Terminal** and run:

```bash
curl http://192.168.50.206:2947/health
```

Expected: `{"status":"ok"}`.
If it hangs or refuses: re-check Part B, confirm both devices are on the same subnet, and disable
**Wi-Fi client/AP isolation** on the router (it blocks device-to-device traffic).

---

## Part D — Get the extension folder onto the Mac

The extension is just static files. Pick **one** way, and put it somewhere **permanent** (an
unpacked extension loads from this path on every Chrome launch — don't delete or move it):

**Option 1 — clone the repo (simplest if git is installed):**
```bash
cd ~ && git clone https://github.com/singyiu/agentic-browser.git
# the extension is then at:  ~/agentic-browser/extension
```

**Option 2 — copy just the folder over SSH from the guardian:**
```bash
mkdir -p ~/aegis && scp -r cyngn@192.168.50.206:/home/cyngn/sing/agentic-browser/extension ~/aegis/extension
# the extension is then at:  ~/aegis/extension
```

(AirDrop / USB also work — you only need the `extension/` folder.)

---

## Part E — Drop in the kid's config  (on the kid Mac)

Create a file named **`guardian-config.json`** *inside* the extension folder (same level as
`manifest.json`), containing the token from Part A:

```bash
cat > ~/agentic-browser/extension/guardian-config.json <<'JSON'
{ "token": "PASTE_THE_KID_TOKEN_HERE", "endpoint": "http://192.168.50.206:2947" }
JSON
```

(Adjust the path if you used Option 2, e.g. `~/aegis/extension/guardian-config.json`.)
The extension reads this on startup; without it, it falls back to an empty token + localhost and
won't authenticate.

---

## Part F — Load the extension in Chrome  (on the kid Mac)

1. Open Chrome → go to `chrome://extensions`.
2. Turn on **Developer mode** (top-right toggle).
3. Click **Load unpacked** → select the **`extension`** folder (the one containing `manifest.json`).
4. **Agentic Parental Control** appears, ID `kmnemdhnpddlknbaiggdnolchnlpgkjl`. Click the puzzle-piece
   and **pin** it so the screen-time icon is visible.

> Tip: do this in a **dedicated Chrome profile** for the kid (Chrome → profile menu → Add) so the
> parent's browsing isn't affected.

---

## Part G — Verify end-to-end

On the kid Mac, in Chrome:

1. Visit an educational page (e.g. `https://en.wikipedia.org/wiki/Photosynthesis`) → it loads
   (allowed). The first hit on a new page may take a couple of seconds (a real model call).
2. Visit something clearly off-limits → the **Aegis block page** appears with a *Request access*
   button.
3. Click the pinned toolbar icon → the screen-time popup shows.

On the guardian dashboard (`http://192.168.50.206:2947/`, parent PIN):

4. **Activity** → you see this kid's allow/block timeline.
5. **Requests** → any *Request access* the kid submitted appears for you to approve/reject.

If pages are *not* being classified: re-check `guardian-config.json` (valid JSON, correct token +
endpoint), reload the extension at `chrome://extensions`, and re-run the Part C health check.

---

## Keeping it running

- The guardian must stay up. It's installed as a systemd `--user` service on the Linux box and
  **auto-starts + survives logout** (`aegis-guardian.service`, linger enabled) — nothing to do.
- If the guardian's IP changes, update `endpoint` in `guardian-config.json` and reload the
  extension. A DHCP reservation/static IP for the guardian avoids this.
- More kids: repeat Parts A–G, one profile + token + Mac each. Each kid gets isolated rules.

---

## Optional: lock it down (kid can't disable the extension)

The unpacked load above can be switched off by the kid at `chrome://extensions`. To **force-install**
it (no disable/remove) on a Mac with Google Chrome you'd use a macOS **managed policy**
(`com.google.Chrome` → `ExtensionSettings` → `installation_mode: force_installed` + `force_pinned`)
pointing at a **self-hosted signed CRX + update manifest**. That requires packing/signing the
extension (any Chromium can `--pack-extension`) and serving the CRX (the guardian already has
`/ext/<profile>/aegis.crx` + `/ext/<profile>/updates.xml` routes, but nothing is packed on this
Linux host yet).

This is a separate, more involved step. If you want the tamper-proof lock, ask and I'll set up the
signing key + CRX packing (either on the Mac with Chrome's `--pack-extension`, or here using
Playwright's Chromium) and generate the matching macOS `.mobileconfig`/managed-preferences policy.
```
