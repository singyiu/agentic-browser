// Parental-control service worker: watch navigations, ask the guardian backend to
// classify the page, and tell the content script to block if unsuitable. Fail-open:
// any error/timeout leaves the page visible.

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

let CONFIG = null;
const hotCache = new Map(); // rawUrl -> { verdict, reason, ts }

async function getConfig() {
  if (CONFIG) return CONFIG;
  try {
    const resp = await fetch(chrome.runtime.getURL("guardian-config.json"));
    CONFIG = await resp.json();
  } catch (_e) {
    CONFIG = { token: "", endpoint: "http://127.0.0.1:2947" };
  }
  return CONFIG;
}

function shouldSkip(url) {
  return !url || HARD_ALLOW.some((re) => re.test(url));
}

async function sendToTab(tabId, message, retries = 3) {
  for (let i = 0; i < retries; i += 1) {
    try {
      return await chrome.tabs.sendMessage(tabId, message);
    } catch (_e) {
      await new Promise((r) => setTimeout(r, 200));
    }
  }
  return null;
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

  const cached = hotCache.get(url);
  if (cached && Date.now() - cached.ts < HOT_TTL_MS) {
    if (cached.verdict === "block")
      await sendToTab(tabId, { type: "BLOCK", reason: cached.reason });
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
  if (result.verdict === "block") {
    await sendToTab(tabId, {
      type: "BLOCK",
      reason: result.reason || "This page is not suitable.",
    });
  }
}

chrome.webNavigation.onCommitted.addListener((d) => {
  if (d.frameId === 0) handleNavigation(d.tabId, d.url);
});
chrome.webNavigation.onHistoryStateUpdated.addListener((d) => {
  if (d.frameId === 0) handleNavigation(d.tabId, d.url);
});
