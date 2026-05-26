// Populate the block page from query params (MV3 extension pages disallow inline JS).
const params = new URLSearchParams(location.search);
const reason = params.get("reason");
const blockedUrl = params.get("url");
// Search mode: a blocked search query rather than a blocked page (kind=search&query=...).
const searchQuery = params.get("query") || "";
const isSearch = params.get("kind") === "search" && !!searchQuery;
if (reason) document.getElementById("reason").textContent = reason;
if (blockedUrl) document.getElementById("url").textContent = blockedUrl;
if (isSearch) {
  // Show what was blocked (the query) instead of a page reason. textContent = XSS-safe.
  const heading = document.querySelector("h1");
  if (heading) heading.textContent = "This search isn't allowed";
  document.getElementById("reason").textContent = searchQuery;
}
document.getElementById("back").addEventListener("click", () => history.back());

// Inline guardian config loader. block.html is an extension page, so it may read the
// bundled, web-accessible config (token + endpoint); arbitrary web pages cannot. Kept
// inline (block.js is a plain script, not a module) to avoid a module/CSP change.
let CONFIG = null;
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

const noteEl = document.getElementById("note");
const requestBtn = document.getElementById("request-btn");
const checkBtn = document.getElementById("check-btn");
const statusEl = document.getElementById("request-status");

function setStatus(msg) {
  statusEl.textContent = msg;
}

function showCheckUi() {
  requestBtn.hidden = true;
  noteEl.hidden = true;
  checkBtn.hidden = false;
}

async function statusFor(cfg) {
  // In search mode, poll the keyword request by query; otherwise the URL access-request.
  const path = isSearch
    ? `/search-request?query=${encodeURIComponent(searchQuery)}`
    : `/access-request?url=${encodeURIComponent(blockedUrl)}`;
  const resp = await fetch(`${cfg.endpoint}${path}`, {
    headers: { "X-Guardian-Token": cfg.token },
  });
  if (!resp.ok) return null;
  return resp.json();
}

async function submitRequest() {
  if (isSearch ? !searchQuery : !blockedUrl) return;
  requestBtn.disabled = true;
  setStatus("Sending…");
  try {
    const cfg = await getConfig();
    const path = isSearch ? "/search-request" : "/access-request";
    const payload = isSearch
      ? { query: searchQuery, url: blockedUrl, note: noteEl.value.trim() }
      : { url: blockedUrl, reason: reason || "", note: noteEl.value.trim() };
    const resp = await fetch(`${cfg.endpoint}${path}`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Guardian-Token": cfg.token,
      },
      body: JSON.stringify(payload),
    });
    if (resp.ok) {
      showCheckUi();
      setStatus(
        "Request sent. Ask your parent to review it, then tap “Check if approved”.",
      );
    } else {
      setStatus("Couldn't send the request. Try again later.");
      requestBtn.disabled = false;
    }
  } catch (_e) {
    setStatus("Couldn't reach the guardian service.");
    requestBtn.disabled = false;
  }
}

async function openIfApproved() {
  // Only ever navigate to a real http(s) page (the URL came from a query param).
  if (!/^https?:\/\//i.test(blockedUrl || "")) return;
  // Drop the service worker's 5-min in-memory verdict so the navigation re-checks the
  // backend (which now allows the freshly whitelisted URL) instead of re-blocking.
  try {
    await chrome.runtime.sendMessage({
      type: "CLEAR_HOTCACHE",
      url: blockedUrl,
    });
  } catch (_e) {
    /* SW may be asleep; the navigation will still re-classify */
  }
  location.replace(blockedUrl);
}

async function checkApproved() {
  if (!blockedUrl) return;
  checkBtn.disabled = true;
  setStatus("Checking…");
  try {
    const cfg = await getConfig();
    const data = await statusFor(cfg);
    if (data && data.status === "approved") {
      setStatus("Approved! Opening…");
      await openIfApproved();
    } else if (data && data.status === "rejected") {
      setStatus(
        data.decision_note
          ? `Your parent said no: ${data.decision_note}`
          : "Your parent didn't approve this.",
      );
      checkBtn.disabled = false;
    } else {
      setStatus("Not yet — still waiting for your parent.");
      checkBtn.disabled = false;
    }
  } catch (_e) {
    setStatus("Couldn't reach the guardian service.");
    checkBtn.disabled = false;
  }
}

requestBtn.addEventListener("click", submitRequest);
checkBtn.addEventListener("click", checkApproved);

// On load, restore the UI if a request for this URL already exists (survives reloads).
async function restoreState() {
  if (!blockedUrl) return;
  try {
    const cfg = await getConfig();
    const data = await statusFor(cfg);
    if (!data) return;
    if (data.status === "approved") {
      showCheckUi();
      setStatus("Approved! Tap “Check if approved” to open the page.");
    } else if (data.status === "pending") {
      showCheckUi();
      setStatus("Your request is waiting for your parent's review.");
    } else if (data.status === "rejected") {
      setStatus("A previous request wasn't approved. You can ask again.");
    }
  } catch (_e) {
    /* leave the default state on any error */
  }
}
restoreState();
