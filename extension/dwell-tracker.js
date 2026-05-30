// Time-on-page tracking. Times the page in the active, focused tab and reports the
// elapsed time to the backend whenever that page stops being the focused one (tab
// switch, window blur, navigation, or tab close).
//
// The page being timed is read directly from the tab at activation time (no remembered
// per-tab state to lose), and mirrored in chrome.storage.session so a service-worker
// restart mid-browsing does not double-count. Dwell is best-effort: a lost event (e.g.
// SW killed before a flush) is acceptable.

import { postDwell } from "./dwell-poster.js";

const CURRENT_KEY = "dwell_current"; // { tabId, urlKey, since }
const MIN_DWELL_MS = 500; // ignore sub-half-second blips (rapid tab cycling)
const SKIP = /^(chrome|chrome-extension|about|devtools|view-source):/;

async function getCurrent() {
  const o = await chrome.storage.session.get(CURRENT_KEY);
  return o[CURRENT_KEY] || null;
}

async function setCurrent(current) {
  await chrome.storage.session.set({ [CURRENT_KEY]: current });
}

async function clearCurrent() {
  await chrome.storage.session.remove(CURRENT_KEY);
}

async function flush() {
  const cur = await getCurrent();
  await clearCurrent();
  if (cur && cur.urlKey && cur.since) {
    const dwellMs = Date.now() - cur.since;
    if (dwellMs >= MIN_DWELL_MS) {
      console.log(`[dwell] flush ${cur.urlKey} ${dwellMs}ms`);
      await postDwell(cur.urlKey, dwellMs);
    }
  }
}

// Begin timing the page currently loaded in a tab. Reads the URL straight from the tab,
// so it does not depend on having observed the navigation beforehand.
async function startTiming(tabId) {
  let url = null;
  try {
    const tab = await chrome.tabs.get(tabId);
    url = tab && tab.url;
  } catch (_e) {
    url = null;
  }
  if (!url || SKIP.test(url)) {
    await clearCurrent();
    return;
  }
  console.log(`[dwell] start tab=${tabId} ${url}`);
  await setCurrent({ tabId, urlKey: url, since: Date.now() });
}

async function isActiveFocused(tabId) {
  try {
    const tab = await chrome.tabs.get(tabId);
    if (!tab || !tab.active) return false;
    const win = await chrome.windows.get(tab.windowId);
    return !!win.focused;
  } catch (_e) {
    return false;
  }
}

// A navigation committed in a tab. Restart timing if it is the page being timed (new
// URL); otherwise begin timing if this tab is the active, focused one.
export async function notifyUrlKey(tabId, _url) {
  const cur = await getCurrent();
  if (cur && cur.tabId === tabId) {
    await flush();
    await startTiming(tabId);
  } else if (!cur && (await isActiveFocused(tabId))) {
    await startTiming(tabId);
  }
}

export async function handleActivated(tabId) {
  console.log(`[dwell] activated tab=${tabId}`);
  await flush();
  await startTiming(tabId);
}

export async function handleFocusChanged(windowId) {
  if (windowId === chrome.windows.WINDOW_ID_NONE) {
    await flush(); // browser lost focus — stop the clock
    return;
  }
  try {
    const [tab] = await chrome.tabs.query({ active: true, windowId });
    if (tab) await startTiming(tab.id);
  } catch (_e) {
    // ignore
  }
}

export async function handleRemoved(tabId) {
  const cur = await getCurrent();
  if (cur && cur.tabId === tabId) await flush();
}

export function handleAlarm() {
  // No-op: the keepalive alarm exists only to wake the service worker periodically so
  // dwell flushes stay responsive during active browsing.
}

// Flush the time elapsed so far WITHOUT ending the segment, then reset the clock to now. The
// periodic heartbeat calls this so a long single-page session (e.g. a 2-hour video on one tab)
// still depletes the budget — and can be blocked — before the user ever navigates away.
export async function flushPartial() {
  const cur = await getCurrent();
  if (!cur || !cur.urlKey || !cur.since) return;
  const now = Date.now();
  const dwellMs = now - cur.since;
  if (dwellMs >= MIN_DWELL_MS) {
    await postDwell(cur.urlKey, dwellMs);
    await setCurrent({ ...cur, since: now });
  }
}

// Pause the clock when the user goes idle or locks the screen, and resume on activity. This is
// what makes accounting count ACTIVE browsing only: a tab left focused while the user is away
// no longer burns the budget. Driven by chrome.idle.onStateChanged in the service worker.
export async function handleIdleState(state) {
  if (state === "active") {
    try {
      const [tab] = await chrome.tabs.query({
        active: true,
        lastFocusedWindow: true,
      });
      if (tab) await startTiming(tab.id);
    } catch (_e) {
      // ignore
    }
  } else {
    await flush(); // idle / locked -> bank the time up to now and stop the clock
  }
}
