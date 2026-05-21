// Best-effort reporting of time-on-page to the guardian backend. Dwell is telemetry,
// so a failed POST is swallowed and never affects browsing.

import { getConfig } from "./guardian-client.js";

export async function postDwell(urlKey, dwellMs) {
  if (!urlKey || dwellMs <= 0) return;
  const cfg = await getConfig();
  try {
    await fetch(`${cfg.endpoint}/dwell`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Guardian-Token": cfg.token,
      },
      body: JSON.stringify({ url_key: urlKey, dwell_ms: Math.round(dwellMs) }),
    });
  } catch (_e) {
    // best-effort telemetry — ignore failures
  }
}
