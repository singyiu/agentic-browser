// Runs in every page. Extracts content for classification and, on command, fades
// the page out and replaces it with the block page.

function extractContent() {
  const meta = (selector) => document.querySelector(selector)?.getAttribute("content") || "";
  let body = "";
  try {
    body = (document.body?.innerText || "").replace(/\s+/g, " ").trim().slice(0, 2000);
  } catch (_e) {
    body = "";
  }

  const data = {
    url: location.href,
    title: document.title || "",
    meta_desc: meta('meta[name="description"]'),
    og_title: meta('meta[property="og:title"]'),
    og_desc: meta('meta[property="og:description"]'),
    body_snippet: body,
  };

  // YouTube enrichment (best-effort; falls back to og tags / title above).
  try {
    if (location.hostname.endsWith("youtube.com") && window.ytInitialData) {
      const contents =
        window.ytInitialData?.contents?.twoColumnWatchNextResults?.results?.results?.contents;
      if (Array.isArray(contents)) {
        const primary = contents.find((c) => c.videoPrimaryInfoRenderer)?.videoPrimaryInfoRenderer;
        const secondary = contents.find((c) => c.videoSecondaryInfoRenderer)
          ?.videoSecondaryInfoRenderer;
        const title = primary?.title?.runs?.[0]?.text;
        const channel = secondary?.owner?.videoOwnerRenderer?.title?.runs?.[0]?.text;
        const desc =
          secondary?.attributedDescription?.content ||
          secondary?.description?.runs?.map((r) => r.text).join("") ||
          "";
        if (title) data.title = title;
        if (channel) data.og_title = `Channel: ${channel}`;
        if (desc) data.body_snippet = `${desc} ${data.body_snippet}`.slice(0, 2000);
      }
    }
  } catch (_e) {
    /* ignore enrichment errors */
  }

  return data;
}

let overlayEl = null;
function ensureOverlay() {
  if (overlayEl) return overlayEl;
  const el = document.createElement("div");
  el.id = "__guardian_overlay";
  el.style.cssText =
    "position:fixed;inset:0;z-index:2147483647;background:#101426;opacity:0;" +
    "transition:opacity .3s ease;pointer-events:none;";
  (document.documentElement || document.body).appendChild(el);
  overlayEl = el;
  return el;
}

function blockPage(reason) {
  const el = ensureOverlay();
  el.style.pointerEvents = "all";
  requestAnimationFrame(() => {
    el.style.opacity = "1";
  });
  setTimeout(() => {
    const params = `?reason=${encodeURIComponent(reason || "")}&url=${encodeURIComponent(location.href)}`;
    location.replace(chrome.runtime.getURL("block.html") + params);
  }, 320);
}

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message.type === "EXTRACT_CONTENT") {
    sendResponse(extractContent());
    return true;
  }
  if (message.type === "BLOCK") {
    blockPage(message.reason);
    sendResponse({ ok: true });
    return true;
  }
  return false;
});
