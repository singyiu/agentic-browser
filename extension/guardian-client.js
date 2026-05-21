// Shared access to the guardian backend config (token + endpoint), written to
// guardian-config.json at launch. Cached after first read. Imported by the service
// worker and the dwell poster so there is one source of truth.

let CONFIG = null;

export async function getConfig() {
  if (CONFIG) return CONFIG;
  try {
    const resp = await fetch(chrome.runtime.getURL("guardian-config.json"));
    CONFIG = await resp.json();
  } catch (_e) {
    CONFIG = { token: "", endpoint: "http://127.0.0.1:2947" };
  }
  return CONFIG;
}
