// Parental-control service worker: watch navigations, ask the guardian backend to
// classify the page, and tell the content script to block if unsuitable. Fail-open:
// any error/timeout leaves the page visible. Runs as an ES module (see manifest).

import { getConfig } from "./guardian-client.js";
import {
  handleActivated,
  handleAlarm,
  handleFocusChanged,
  handleRemoved,
  notifyUrlKey,
} from "./dwell-tracker.js";

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
async function blockTab(tabId, reason, pageUrl) {
  sendToTab(tabId, { type: "BLOCK", reason });
  const params =
    `?reason=${encodeURIComponent(reason || "")}` +
    `&url=${encodeURIComponent(pageUrl || "")}`;
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

async function handleNavigation(tabId, url) {
  if (shouldSkip(url)) return;
  console.log(`[guardian] nav tab=${tabId} url=${url}`);
  notifyUrlKey(tabId, url); // (re)start dwell timing for the active page

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
    sendResponse({ ok: true });
  }
  return false; // synchronous response
});

// Dwell-time tracking: time-on-page per active/focused tab, reported to the backend.
chrome.tabs.onActivated.addListener((info) => handleActivated(info.tabId));
chrome.tabs.onRemoved.addListener((tabId) => handleRemoved(tabId));
chrome.windows.onFocusChanged.addListener((winId) => handleFocusChanged(winId));
chrome.alarms.create("guardian-dwell-keepalive", { periodInMinutes: 0.5 });
chrome.alarms.onAlarm.addListener(handleAlarm);
