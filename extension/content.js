// Runs in every page. Extracts content for classification and, on command, fades
// the page out and replaces it with the block page.

function extractContent() {
  const meta = (selector) =>
    document.querySelector(selector)?.getAttribute("content") || "";
  let body = "";
  try {
    body = (document.body?.innerText || "")
      .replace(/\s+/g, " ")
      .trim()
      .slice(0, 2000);
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
        window.ytInitialData?.contents?.twoColumnWatchNextResults?.results
          ?.results?.contents;
      if (Array.isArray(contents)) {
        const primary = contents.find(
          (c) => c.videoPrimaryInfoRenderer,
        )?.videoPrimaryInfoRenderer;
        const secondary = contents.find(
          (c) => c.videoSecondaryInfoRenderer,
        )?.videoSecondaryInfoRenderer;
        const title = primary?.title?.runs?.[0]?.text;
        const channel =
          secondary?.owner?.videoOwnerRenderer?.title?.runs?.[0]?.text;
        const desc =
          secondary?.attributedDescription?.content ||
          secondary?.description?.runs?.map((r) => r.text).join("") ||
          "";
        if (title) data.title = title;
        if (channel) data.og_title = `Channel: ${channel}`;
        if (desc)
          data.body_snippet = `${desc} ${data.body_snippet}`.slice(0, 2000);
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
    "position:fixed;inset:0;z-index:2147483647;background:#faf5ed;opacity:0;" +
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
  if (message.type === "UPDATE_TIME_HUD") {
    updateHud(message.state);
    sendResponse({ ok: true });
    return true;
  }
  return false;
});

/* --- In-page time HUD ------------------------------------------------------------------------
   A small fixed pill showing the kid's remaining general screen-time, plus per-site credits when
   the current site has its own rule. Click to expand a panel with a "Request more time" button.
   Inline-styled (CSP-safe on any page) and hidden entirely when no time limit is configured.
   State is pushed by the service worker via UPDATE_TIME_HUD (per navigation + every 30s). */

const HUD_ID = "__guardian_time_hud";
let hudRefs = null;

function fmtRemaining(ms) {
  const min = Math.max(0, Math.round((ms || 0) / 60000));
  const h = Math.floor(min / 60);
  const m = min % 60;
  if (h && m) return h + "h " + m + "m";
  if (h) return h + "h";
  return m + "m";
}

function buildHud() {
  if (hudRefs) return hudRefs;
  const root = document.createElement("div");
  root.id = HUD_ID;
  root.style.cssText =
    "position:fixed;right:16px;bottom:16px;z-index:2147483646;" +
    "font-family:system-ui,-apple-system,'Segoe UI',Roboto,sans-serif;";

  const pill = document.createElement("button");
  pill.type = "button";
  pill.setAttribute("aria-expanded", "false");
  pill.style.cssText =
    "display:inline-flex;align-items:center;gap:6px;border:none;cursor:pointer;" +
    "border-radius:999px;padding:8px 14px;font-size:13px;font-weight:600;color:#faf5ed;" +
    "background:#4a3f35;box-shadow:0 2px 10px rgba(0,0,0,.25);";

  const panel = document.createElement("div");
  panel.hidden = true;
  panel.style.cssText =
    "margin-top:8px;width:260px;background:#faf5ed;color:#3c3228;border:1px solid #e4d9c8;" +
    "border-radius:14px;padding:14px;box-shadow:0 6px 24px rgba(0,0,0,.18);font-size:13px;";

  const general = document.createElement("p");
  general.style.cssText = "margin:0 0 8px;font-weight:600;";
  const siteLine = document.createElement("p");
  siteLine.style.cssText = "margin:0 0 8px;color:#6b5d4d;";

  const reason = document.createElement("input");
  reason.type = "text";
  reason.maxLength = 200;
  reason.placeholder = "Why do you need more time?";
  reason.style.cssText =
    "width:100%;box-sizing:border-box;margin-bottom:8px;padding:7px 9px;" +
    "border:1px solid #e4d9c8;border-radius:8px;font-size:13px;";

  const ask = document.createElement("button");
  ask.type = "button";
  ask.textContent = "Request more time";
  ask.style.cssText =
    "width:100%;border:none;cursor:pointer;border-radius:8px;padding:8px;font-size:13px;" +
    "font-weight:600;color:#faf5ed;background:#c0563a;";

  const status = document.createElement("p");
  status.style.cssText = "margin:8px 0 0;color:#6b5d4d;min-height:1em;";

  panel.append(general, siteLine, reason, ask, status);
  root.append(pill, panel);
  (document.documentElement || document.body).appendChild(root);

  pill.addEventListener("click", () => {
    const open = panel.hidden;
    panel.hidden = !open;
    pill.setAttribute("aria-expanded", open ? "true" : "false");
  });
  ask.addEventListener("click", async () => {
    ask.disabled = true;
    status.textContent = "Sending…";
    try {
      const res = await chrome.runtime.sendMessage({
        type: "TIME_REQUEST",
        reason: reason.value.trim(),
      });
      if (res && res.ok) {
        status.textContent = "Sent! Ask your parent to approve it.";
      } else {
        status.textContent = "Couldn't send — try again later.";
        ask.disabled = false;
      }
    } catch (_e) {
      status.textContent = "Couldn't reach the guardian.";
      ask.disabled = false;
    }
  });

  hudRefs = { root, pill, panel, general, siteLine };
  return hudRefs;
}

function updateHud(state) {
  const g = state && state.general;
  // No general limit configured -> don't clutter the page.
  if (!g || g.limit_ms == null) {
    if (hudRefs) hudRefs.root.style.display = "none";
    return;
  }
  const h = buildHud();
  h.root.style.display = "block";
  const remaining = g.remaining_ms == null ? 0 : g.remaining_ms;
  h.pill.textContent = "⏳ " + fmtRemaining(remaining) + " left";
  // Urgency colour: red under 5 min, amber under 15, calm otherwise.
  h.pill.style.background =
    remaining <= 5 * 60000
      ? "#c0563a"
      : remaining <= 15 * 60000
        ? "#b8893a"
        : "#4a3f35";
  h.general.textContent =
    "General: " +
    fmtRemaining(remaining) +
    " left of " +
    fmtRemaining(g.limit_ms) +
    " today";
  const site = state.site;
  if (site && site.host && !state.bedtime?.active) {
    if (site.excluded) {
      h.siteLine.textContent = "This site doesn't count against your time.";
    } else if (site.limit_ms != null) {
      h.siteLine.textContent =
        "This site: " +
        fmtRemaining(site.remaining_ms) +
        " left of " +
        fmtRemaining(site.limit_ms);
    } else {
      h.siteLine.textContent = "";
    }
  } else {
    h.siteLine.textContent = "";
  }
  h.siteLine.style.display = h.siteLine.textContent ? "block" : "none";
}

/* --- Kid-safe search enforcement -------------------------------------------------------------
   Intercept search/prompt submissions in-page — traditional search boxes AND AI-chat inputs on
   ChatGPT / Claude / Perplexity — check the typed query with the guardian, and block unsafe ones.
   The check is async, so we HOLD the submission (preventDefault + stopImmediatePropagation), ask
   the backend via the service worker (a content script can't call it directly — CORS), then
   either replay the user's action (allowed, or unknown => fail-open) or show the search block
   page. Capture-phase, delegated listeners on document handle dynamically-rendered SPA inputs. */

const AI_CHAT_HOSTS = new Set([
  "chatgpt.com",
  "chat.openai.com",
  "claude.ai",
  "perplexity.ai",
]);
const SEARCH_INPUT_SELECTOR =
  'input[type="search"], input[name="q"], input[name="search_query"], input[name="p"], form[role="search"] input';
const SEND_BUTTON_SELECTOR =
  'button[type="submit"], button[data-testid="send-button"], button[aria-label*="send" i]';

let _replaying = false; // true while we re-issue an allowed submission, so our handlers skip it

function isChatHost() {
  return AI_CHAT_HOSTS.has(location.hostname.replace(/^www\./, ""));
}

function chatInputText(target) {
  if (!isChatHost() || !target) return "";
  if (target.tagName === "TEXTAREA") return (target.value || "").trim();
  if (target.isContentEditable && typeof target.closest === "function") {
    const root = target.closest("[contenteditable='true']") || target;
    return (root.innerText || "").trim();
  }
  return "";
}

function searchInputText(target) {
  if (!target || typeof target.matches !== "function") return "";
  return target.matches(SEARCH_INPUT_SELECTOR)
    ? (target.value || "").trim()
    : "";
}

function findSendButton(target) {
  const scope =
    (target.closest && target.closest("form, [role='dialog'], main")) ||
    document.body ||
    document;
  return scope.querySelector(SEND_BUTTON_SELECTOR);
}

function navigateToBlockPage(reason, query) {
  const params = new URLSearchParams({
    reason: reason || "This search isn't allowed.",
    url: location.href,
    kind: "search",
    query,
  });
  location.replace(
    chrome.runtime.getURL("block.html") + "?" + params.toString(),
  );
}

// Ask the SW to classify the query, then either block or replay the held action. Fail-open: any
// error (or a missing verdict) replays the action so an allowed search is never broken.
async function guardThenReplay(query, replay) {
  let result = null;
  try {
    result = await chrome.runtime.sendMessage({
      type: "CLASSIFY_SEARCH",
      query,
    });
  } catch (_e) {
    result = null;
  }
  if (result && result.verdict === "block") {
    navigateToBlockPage(result.reason, query);
    return;
  }
  _replaying = true;
  try {
    replay();
  } catch (_e) {
    /* the query was allowed; if the replay fails there is nothing to block */
  } finally {
    setTimeout(() => {
      _replaying = false;
    }, 0);
  }
}

function replayKeydown(target) {
  if (isChatHost()) {
    const btn = findSendButton(target);
    if (btn) {
      btn.click();
      return;
    }
  } else if (target.form) {
    target.form.submit();
    return;
  }
  target.dispatchEvent(
    new KeyboardEvent("keydown", {
      key: "Enter",
      bubbles: true,
      cancelable: true,
    }),
  );
}

async function onKeydownCapture(e) {
  if (_replaying || e.key !== "Enter" || e.shiftKey || e.isComposing) return;
  const query = chatInputText(e.target) || searchInputText(e.target);
  if (!query) return;
  e.preventDefault();
  e.stopImmediatePropagation();
  await guardThenReplay(query, () => replayKeydown(e.target));
}

async function onSubmitCapture(e) {
  if (_replaying) return;
  const form = e.target;
  if (!form || typeof form.querySelector !== "function") return;
  const input = form.querySelector(SEARCH_INPUT_SELECTOR);
  const query = input ? (input.value || "").trim() : "";
  if (!query) return;
  e.preventDefault();
  e.stopImmediatePropagation();
  await guardThenReplay(query, () => form.submit());
}

async function onClickCapture(e) {
  if (_replaying || !isChatHost()) return;
  const target = e.target;
  if (!target || typeof target.closest !== "function") return;
  const btn = target.closest(SEND_BUTTON_SELECTOR);
  if (!btn) return;
  const input = document.querySelector("textarea, [contenteditable='true']");
  const query = input ? (input.value || input.innerText || "").trim() : "";
  if (!query) return;
  e.preventDefault();
  e.stopImmediatePropagation();
  await guardThenReplay(query, () => btn.click());
}

document.addEventListener("keydown", onKeydownCapture, true);
document.addEventListener("submit", onSubmitCapture, true);
document.addEventListener("click", onClickCapture, true);
