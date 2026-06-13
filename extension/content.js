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

  // NOTE: no page-variable enrichment (e.g. window.ytInitialData) — MV3 content scripts
  // run in an isolated world where page JS globals are never visible, so YouTube pages
  // are described by their og: tags, title, and body text like any other site.
  return {
    url: location.href,
    title: document.title || "",
    meta_desc: meta('meta[name="description"]'),
    og_title: meta('meta[property="og:title"]'),
    og_desc: meta('meta[property="og:description"]'),
    body_snippet: body,
  };
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
  return false;
});

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

// One pending classification per query: a rapid double-Enter (or double-click on Send)
// arrives before the first check resolves, passes the _replaying guard (still false),
// and would submit twice. Both events now share the same in-flight promise instead.
const _inflight = new Map(); // query -> Promise<void>

// Ask the SW to classify the query, then either block or replay the held action. Fail-open: any
// error (or a missing verdict) replays the action so an allowed search is never broken.
function guardThenReplay(query, replay) {
  const pending = _inflight.get(query);
  if (pending) return pending; // duplicate submit while the check is pending — replay once
  const run = guardThenReplayOnce(query, replay).finally(() =>
    _inflight.delete(query),
  );
  _inflight.set(query, run);
  return run;
}

async function guardThenReplayOnce(query, replay) {
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
