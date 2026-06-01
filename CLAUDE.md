# CLAUDE.md — agentic-browser

## Kid browser: auto-relaunch on extension changes (standing preference)

When you change the kid extension (anything under `extension/`), **automatically repack, redeploy,
and relaunch the local kid browser** so the change takes effect — do **not** leave activation as a
manual step for the user.

The kid browser runs **locked**: force-installed CRX via managed-preferences policy
(`/Library/Managed Preferences/org.chromium.Chromium.plist`); user-data-dir
`agent-backend/.chromium-profile`; CDP `:9222`; extension ID `kmnemdhnpddlknbaiggdnolchnlpgkjl`.

Reliable activation procedure:

1. Bump `extension/manifest.json` `version`, then `bash agent-backend/scripts/pack-extension.sh`
   (reads the token from `agent-backend/.env`, signs with `.secrets/aegis-ext.pem`, writes
   `.chromium-dist/aegis.crx` + `updates.xml`). The guardian serves `/ext/*` from disk, so **no
   guardian restart**; the ID is stable, so **no `sudo` policy reinstall**.
2. **This Chromium build has no working in-session auto-update for force-installed extensions**
   (zero `update_client`/`CrxInstaller` log activity; `--extensions-update-frequency` is ignored).
   A plain relaunch reloads the cached version; deleting only the unpacked folder leaves it broken
   (`Failed to get file path to content.js`). Force a fresh **bootstrap**: with the browser stopped,
   make the extension "unknown" — in `agent-backend/.chromium-profile/Default/Secure Preferences`
   delete `extensions.settings.<ID>` **and** `protection.macs.extensions.settings.<ID>` (back up
   first), do the same in `Default/Preferences`, and `rm -rf Default/Extensions/<ID>`.
3. Stop the browser: SIGTERM every process whose args contain `agent-backend/.chromium-profile`,
   wait, SIGKILL stragglers.
4. Relaunch detached so it outlives the session:
   `subprocess.Popen(["bash","agent-backend/scripts/launch-chromium.sh"], start_new_session=True)`
   (default = locked mode).
5. Verify: poll `Default/Extensions/<ID>/<ver>_0/manifest.json` (the new version installs ~10s after
   bootstrap) and grep the deployed code for the change's markers.

Scope: kid-browser relaunch is for **extension** changes only. Guardian backend changes → restart
the guardian (`launchctl kickstart -k gui/$(id -u)/com.aegis.guardian`). Dashboard/Grafana → neither.

Smoother alternative (needs the user's `sudo`): dev-unpacked mode
`AEGIS_DEV_UNPACKED=1 bash agent-backend/scripts/launch-chromium.sh` loads `extension/` directly
(instant source changes, no repack/scrub) — but it conflicts with the active force-install policy,
so remove the policy first: `sudo bash agent-backend/scripts/uninstall-extension-policy.sh`.
