// Parental-control service worker: watch navigations, ask the guardian backend to
// classify the page, and tell the content script to block if unsuitable. Fail-open:
// any error/timeout leaves the page visible. Runs as an ES module (see manifest).

import { getConfig } from "./guardian-client.js";
import {
  ensureTiming,
  flushPartial,
  handleActivated,
  handleFocusChanged,
  handleIdleState,
  handleRemoved,
  notifyUrlKey,
  pauseTiming,
} from "./dwell-tracker.js";

// How long without user input (keyboard/mouse) counts as "idle". The heartbeat stops banking
// screen time while idle, and chrome.idle transitions fire at this threshold. ~1 min of grace so
// brief reading pauses still count, but a tab left unattended no longer drains the budget.
const IDLE_THRESHOLD_SECONDS = 60;

const HARD_ALLOW = [
  /^chrome:/,
  /^chrome-extension:/,
  /^about:/,
  /^devtools:/,
  /^view-source:/,
  /^https?:\/\/(localhost|127\.0\.0\.1)/,
];
const HOT_TTL_MS = 5 * 60 * 1000;
const TIMEOUT_MS = 185000; // wait out the backend classify budget (>= GUARDIAN_CLASSIFY_TIMEOUT)

const hotCache = new Map(); // rawUrl -> { verdict, reason, ts }

function shouldSkip(url) {
  return !url || HARD_ALLOW.some((re) => re.test(url));
}

async function sendToTab(tabId, message, retries = 3) {
  let lastErr = null;
  for (let i = 0; i < retries; i += 1) {
    try {
      return await chrome.tabs.sendMessage(tabId, message);
    } catch (e) {
      lastErr = e;
      await new Promise((r) => setTimeout(r, 200));
    }
  }
  console.warn(
    `[guardian] sendToTab(${message?.type}) failed for tab ${tabId}:`,
    lastErr?.message || lastErr,
  );
  return null;
}

// Authoritatively block a tab by navigating it to the extension's block page from the
// background. This does NOT depend on the content script being reachable — the prior
// content-script-only path (sendToTab -> location.replace) failed silently whenever the
// script was gone (e.g. YouTube SPA document swaps), leaving the page playing. The
// content-script fade is fired first as a best-effort visual and is intentionally not awaited.
async function blockTab(tabId, reason, pageUrl, opts = {}) {
  sendToTab(tabId, { type: "BLOCK", reason });
  let params =
    `?reason=${encodeURIComponent(reason || "")}` +
    `&url=${encodeURIComponent(pageUrl || "")}`;
  if (opts.kind === "search") {
    params += `&kind=search&query=${encodeURIComponent(opts.query || "")}`;
  } else if (opts.kind === "timelimit") {
    // Without this the block page falls back to the URL access-request flow ("Request access"),
    // so approving it whitelists the page instead of granting time — and the kid stays blocked.
    params += `&kind=timelimit`;
    if (opts.tl) params += `&tl=${encodeURIComponent(opts.tl)}`;
  }
  const blockUrl = chrome.runtime.getURL("block.html") + params;
  try {
    await chrome.tabs.update(tabId, { url: blockUrl });
    console.log(`[guardian] blocked tab ${tabId}: ${pageUrl}`);
  } catch (e) {
    console.warn(
      `[guardian] tabs.update block failed for tab ${tabId}:`,
      e?.message || e,
    );
  }
}

async function classify(payload) {
  const cfg = await getConfig();
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), TIMEOUT_MS);
  try {
    const resp = await fetch(`${cfg.endpoint}/classify`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Guardian-Token": cfg.token,
      },
      body: JSON.stringify(payload),
      signal: controller.signal,
    });
    if (!resp.ok) return { verdict: "allow", reason: "backend_error" };
    return await resp.json();
  } catch (_e) {
    return { verdict: "allow", reason: "unreachable" }; // fail-open
  } finally {
    clearTimeout(timer);
  }
}

// Known search engines -> the URL param that carries the typed query. Unknown hosts fall back
// to ?q= (a near-universal search convention), so lesser-known engines are still covered.
const SEARCH_PARAMS = {
  "google.com": "q",
  "www.google.com": "q",
  "bing.com": "q",
  "www.bing.com": "q",
  "duckduckgo.com": "q",
  "search.yahoo.com": "p",
  "youtube.com": "search_query",
  "www.youtube.com": "search_query",
  "m.youtube.com": "search_query",
};

// Pull the search terms out of a search-engine URL. Returns null when the navigation isn't a
// search, so ordinary page loads skip the keyword check entirely.
function parseSearchQuery(rawUrl) {
  try {
    const u = new URL(rawUrl);
    const param = SEARCH_PARAMS[u.hostname] || "q";
    const trimmed = (u.searchParams.get(param) || "").trim();
    return trimmed ? trimmed : null;
  } catch (_e) {
    return null;
  }
}

// Ask the backend whether a bare search query is allowed for this profile. Fail-open like
// classify(): a backend error/timeout never blocks searching.
async function classifySearchQuery(query) {
  const cfg = await getConfig();
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), TIMEOUT_MS);
  try {
    const resp = await fetch(`${cfg.endpoint}/search-classify`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Guardian-Token": cfg.token,
      },
      body: JSON.stringify({ query }),
      signal: controller.signal,
    });
    if (!resp.ok) return { verdict: "allow", reason: "backend_error" };
    return await resp.json();
  } catch (_e) {
    return { verdict: "allow", reason: "unreachable" }; // fail-open
  } finally {
    clearTimeout(timer);
  }
}

async function captureScreenshot(tabId) {
  try {
    const tab = await chrome.tabs.get(tabId);
    const dataUrl = await chrome.tabs.captureVisibleTab(tab.windowId, {
      format: "png",
    });
    return dataUrl.replace(/^data:image\/png;base64,/, "");
  } catch (_e) {
    return null;
  }
}

// --- Screen-time enforcement (server-authoritative) ---

// Cheap, no-LLM check of this profile's current time budget for a URL. Fail-open: any error
// returns null, so a backend hiccup never blocks browsing.
async function timeState(url) {
  const cfg = await getConfig();
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 8000);
  try {
    const resp = await fetch(
      `${cfg.endpoint}/time/state?url=${encodeURIComponent(url)}`,
      { headers: { "X-Guardian-Token": cfg.token }, signal: controller.signal },
    );
    if (!resp.ok) return null;
    return await resp.json();
  } catch (_e) {
    return null;
  } finally {
    clearTimeout(timer);
  }
}

// A friendly block-page message for why time ran out.
function timeReason(state) {
  const r = state && state.reason;
  if (r === "bedtime")
    return "It's bedtime — browsing is paused until the morning.";
  if (r === "site_limit")
    return "You've used up your time for this site today.";
  return "Time's up — you've used all your screen time for today.";
}

// Forward a kid's "ask for more time" to the backend (a content script can't call it directly —
// CORS). Returns the backend response or a failure marker.
async function submitTimeRequest(message) {
  const cfg = await getConfig();
  try {
    const resp = await fetch(`${cfg.endpoint}/time-request`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Guardian-Token": cfg.token,
      },
      body: JSON.stringify({
        reason: String(message.reason || ""),
        note: String(message.note || ""),
        target_host: message.target_host || undefined,
      }),
    });
    if (!resp.ok) return { ok: false, status: resp.status };
    return { ok: true, ...(await resp.json()) };
  } catch (_e) {
    return { ok: false, error: "unreachable" };
  }
}

async function activeFocusedTab() {
  try {
    const [tab] = await chrome.tabs.query({
      active: true,
      lastFocusedWindow: true,
    });
    return tab || null;
  } catch (_e) {
    return null;
  }
}

// Reflect the general pool's remaining minutes on the toolbar button (badge text + colour).
// Browser-wide (one profile per browser), so a single global badge is correct; per-site detail
// lives in the popup. Empty budget / no policy clears the badge.
function updateActionBadge(state) {
  if (!chrome.action) return;
  const g = state && state.general;
  if (!g || g.limit_ms == null) {
    chrome.action.setBadgeText({ text: "" });
    chrome.action.setTitle({ title: "Screen time" });
    return;
  }
  const remMs = g.remaining_ms == null ? 0 : g.remaining_ms;
  const remMin = Math.max(0, Math.round(remMs / 60000));
  chrome.action.setBadgeText({ text: remMin > 99 ? "99+" : String(remMin) });
  chrome.action.setBadgeBackgroundColor({
    color:
      remMs <= 5 * 60000
        ? "#c0563a"
        : remMs <= 15 * 60000
          ? "#b8893a"
          : "#3f7a4f",
  });
  chrome.action.setBadgeTextColor?.({ color: "#ffffff" });
  chrome.action.setTitle({ title: `${remMin} min of screen time left today` });
}

// Current input-idle state ("active" | "idle" | "locked"). Falls back to "active" if the idle API
// is unavailable, so budget enforcement never silently stops counting.
function idleStateSafe() {
  return new Promise((resolve) => {
    try {
      if (!chrome.idle || !chrome.idle.queryState) return resolve("active");
      chrome.idle.queryState(IDLE_THRESHOLD_SECONDS, (state) =>
        resolve(chrome.runtime.lastError ? "active" : state || "active"),
      );
    } catch (_e) {
      resolve("active");
    }
  });
}

// Periodic (30s alarm) heartbeat: bank the in-progress dwell so the budget is current, then
// re-check the active tab — block it the moment the budget is spent (mid-session, not only on
// the next navigation) and refresh the toolbar badge.
async function timeHeartbeat() {
  // Count active browsing only. Query the live idle state every tick instead of trusting the
  // best-effort idle->idle transition event (MV3 may have torn the worker down when it fired):
  // if idle/locked, stop the clock and bank nothing; if active, bank the interval and make sure
  // the clock is running again in case an earlier idle stretch had paused it.
  if ((await idleStateSafe()) === "active") {
    await flushPartial();
    await ensureTiming();
  } else {
    await pauseTiming();
  }
  const tab = await activeFocusedTab();
  if (!tab || !tab.id || shouldSkip(tab.url)) return;
  const state = await timeState(tab.url);
  if (!state) return;
  updateActionBadge(state);
  if (state.blocked) {
    await blockTab(tab.id, timeReason(state), tab.url, { kind: "timelimit" });
  }
}

async function handleNavigation(tabId, url) {
  if (shouldSkip(url)) return;
  console.log(`[guardian] nav tab=${tabId} url=${url}`);
  notifyUrlKey(tabId, url); // (re)start dwell timing for the active page

  // Screen-time gate (server-authoritative): block when the budget is spent or during a bedtime
  // window. Excluded/educational hosts come back blocked=false, so they stay usable.
  const tState = await timeState(url);
  if (tState) {
    updateActionBadge(tState);
    if (tState.blocked) {
      await blockTab(tabId, timeReason(tState), url, { kind: "timelimit" });
      return;
    }
  }

  // Search-keyword gate: when this navigation is a search, judge the QUERY first. A blocked
  // query goes straight to the search-mode block page; an allowed query falls through to the
  // normal page classification below. Keyed separately ("search:<url>") in the hot cache.
  const searchQuery = parseSearchQuery(url);
  if (searchQuery) {
    const searchKey = `search:${url}`;
    let sc = hotCache.get(searchKey);
    if (!sc || Date.now() - sc.ts >= HOT_TTL_MS) {
      const result = await classifySearchQuery(searchQuery);
      sc = { verdict: result.verdict, reason: result.reason, ts: Date.now() };
      hotCache.set(searchKey, sc);
    }
    if (sc.verdict === "block") {
      await blockTab(tabId, sc.reason || "This search isn't allowed.", url, {
        kind: "search",
        query: searchQuery,
      });
      return;
    }
  }

  const cached = hotCache.get(url);
  if (cached && Date.now() - cached.ts < HOT_TTL_MS) {
    if (cached.verdict === "block") await blockTab(tabId, cached.reason, url);
    return;
  }

  const content = await sendToTab(tabId, { type: "EXTRACT_CONTENT" });
  if (!content) return; // content script not ready -> fail-open

  let result = await classify({ ...content, url, can_escalate: true });

  if (result.verdict === "need_screenshot") {
    const screenshot = await captureScreenshot(tabId);
    if (screenshot) {
      result = await classify({
        ...content,
        url,
        can_escalate: false,
        screenshot_b64: screenshot,
      });
    }
    if (result.verdict === "need_screenshot") {
      result = { verdict: "allow", reason: "screenshot_unavailable" };
    }
  }

  hotCache.set(url, {
    verdict: result.verdict,
    reason: result.reason,
    ts: Date.now(),
  });
  console.log(
    `[guardian] verdict=${result.verdict} reason=${result.reason || ""} url=${url}`,
  );
  if (result.verdict === "block") {
    await blockTab(tabId, result.reason || "This page is not suitable.", url);
  }
}

chrome.webNavigation.onCommitted.addListener((d) => {
  if (d.frameId === 0) handleNavigation(d.tabId, d.url);
});
chrome.webNavigation.onHistoryStateUpdated.addListener((d) => {
  if (d.frameId === 0) handleNavigation(d.tabId, d.url);
});

// The block page asks us to evict a cached verdict after a parent approves a request, so the
// next navigation re-classifies (and the backend now allows the freshly whitelisted URL)
// instead of being re-blocked by the stale 5-minute hot cache.
const BLOCK_PAGE_URL = chrome.runtime.getURL("block.html");
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message?.type === "CLASSIFY_SEARCH") {
    // Proxy a content-script search check through the SW: a content script on a web origin
    // can't call the backend directly (CORS), but the SW holds host permissions. Only honor it
    // from our own extension contexts (defense-in-depth, mirroring CLEAR_HOTCACHE).
    if (sender.id !== chrome.runtime.id) {
      sendResponse({ verdict: "allow", reason: "unauthorized" });
      return false;
    }
    classifySearchQuery(String(message.query || "")).then(sendResponse);
    return true; // async response
  }
  if (message?.type === "TIME_REQUEST") {
    // Proxy the in-page HUD's "ask for more time" through the SW (content scripts can't call the
    // backend directly — CORS). Only honor it from our own extension contexts.
    if (sender.id !== chrome.runtime.id) {
      sendResponse({ ok: false, error: "unauthorized" });
      return false;
    }
    submitTimeRequest(message || {}).then(sendResponse);
    return true; // async response
  }
  if (message?.type === "CLEAR_HOTCACHE") {
    // Only honor this from our own block page — not from content scripts running in web
    // pages (which could otherwise evict a verdict to fish for a fail-open).
    if (
      sender.id !== chrome.runtime.id ||
      !sender.url?.startsWith(BLOCK_PAGE_URL)
    ) {
      sendResponse({ ok: false });
      return false;
    }
    hotCache.delete(message.url);
    hotCache.delete(`search:${message.url}`); // also drop the cached search verdict
    sendResponse({ ok: true });
  }
  return false; // synchronous response
});

// Dwell-time tracking: time-on-page per active/focused tab, reported to the backend.
chrome.tabs.onActivated.addListener((info) => handleActivated(info.tabId));
chrome.tabs.onRemoved.addListener((tabId) => handleRemoved(tabId));
chrome.windows.onFocusChanged.addListener((winId) => handleFocusChanged(winId));
chrome.alarms.create("guardian-dwell-keepalive", { periodInMinutes: 0.5 });
chrome.alarms.onAlarm.addListener((alarm) => {
  // Every 30s: bank in-progress dwell, enforce the budget on the active tab, refresh the HUD.
  if (alarm.name === "guardian-dwell-keepalive") timeHeartbeat();
});

// Count active browsing only: pause the dwell clock when the user is idle / screen-locked.
// Guarded: if the "idle" permission isn't present, never let it crash the worker on startup —
// time enforcement still works via navigation checks + the 30s heartbeat, just without idle pausing.
if (chrome.idle && chrome.idle.onStateChanged) {
  chrome.idle.setDetectionInterval(IDLE_THRESHOLD_SECONDS);
  chrome.idle.onStateChanged.addListener(handleIdleState);
}
