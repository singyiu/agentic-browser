// Populate the block page from query params (MV3 extension pages disallow inline JS).
// Runs as an ES module so the guardian config loader is shared with the service worker.
import { getConfig } from "./guardian-client.js";

const params = new URLSearchParams(location.search);
const reason = params.get("reason");
const blockedUrl = params.get("url");
// Search mode: a blocked search query rather than a blocked page (kind=search&query=...).
const searchQuery = params.get("query") || "";
const isSearch = params.get("kind") === "search" && !!searchQuery;
// Time-limit / bedtime block: the kid asks a parent for more time rather than to unblock a URL.
const isTime = params.get("kind") === "timelimit";
if (reason) document.getElementById("reason").textContent = reason;
if (blockedUrl) document.getElementById("url").textContent = blockedUrl;
if (isSearch) {
  // Show what was blocked (the query) instead of a page reason. textContent = XSS-safe.
  const heading = document.querySelector("h1");
  if (heading) heading.textContent = "This search isn't allowed";
  document.getElementById("reason").textContent = searchQuery;
}
if (isTime) {
  const heading = document.querySelector("h1");
  if (heading) {
    heading.textContent = /bedtime/i.test(reason || "")
      ? "It's bedtime"
      : "Time's up";
  }
  const requestBtnEl = document.getElementById("request-btn");
  if (requestBtnEl) requestBtnEl.textContent = "Request more time";
  const noteFieldEl = document.getElementById("note");
  if (noteFieldEl) {
    noteFieldEl.placeholder =
      "Optional: tell your parent why you need more time";
  }
}
document.getElementById("back").addEventListener("click", () => history.back());

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
  // Time mode polls the per-profile time request; search polls by query; else the URL request.
  const path = isTime
    ? `/time-request`
    : isSearch
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
    const path = isTime
      ? "/time-request"
      : isSearch
        ? "/search-request"
        : "/access-request";
    const payload = isTime
      ? { reason: noteEl.value.trim() }
      : isSearch
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

// --- Prize points: spend points for instant bonus time (time-limit blocks only) ---
// The kid can redeem points for more minutes without waiting for a parent. Only shown when this
// is a time-limit block AND they have enough points for at least one package (within the day cap).
async function initPrizeRedeem() {
  if (!isTime) return;
  let data;
  try {
    const cfg = await getConfig();
    const resp = await fetch(`${cfg.endpoint}/prize-points`, {
      headers: { "X-Guardian-Token": cfg.token },
    });
    if (!resp.ok) return;
    data = await resp.json();
  } catch (_e) {
    return; // offline / no points service → fall back to the request-a-parent flow
  }
  const balance = Number(data.balance || 0);
  const packages = Array.isArray(data.packages) ? data.packages : [];
  if (balance <= 0 || !packages.some((p) => p.affordable)) return;
  document.getElementById("prize-balance").textContent =
    "You have " + balance + (balance === 1 ? " prize point" : " prize points");
  const pkgWrap = document.getElementById("prize-packages");
  pkgWrap.replaceChildren();
  for (const p of packages) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "pkg";
    btn.textContent = "+" + p.minutes + " min · " + p.cost + " pts";
    btn.dataset.afford = p.affordable ? "1" : "";
    btn.disabled = !p.affordable;
    btn.addEventListener("click", () => redeem(p.minutes));
    pkgWrap.append(btn);
  }
  document.getElementById("prize-section").hidden = false;
}

async function redeem(minutes) {
  const status = document.getElementById("prize-status");
  const btns = [...document.querySelectorAll("#prize-packages .pkg")];
  btns.forEach((b) => (b.disabled = true));
  status.textContent = "Redeeming…";
  try {
    const cfg = await getConfig();
    const resp = await fetch(`${cfg.endpoint}/prize-points/redeem`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Guardian-Token": cfg.token,
      },
      body: JSON.stringify({ minutes }),
    });
    const data = await resp.json().catch(() => ({}));
    if (resp.ok && data.ok) {
      status.textContent = "You got " + minutes + " more minutes! Opening…";
      await openAfterRedeem();
    } else {
      status.textContent =
        data.error === "daily_cap_reached"
          ? "You've hit today's bonus-time limit."
          : data.error === "insufficient_points"
            ? "You don't have enough points."
            : "Couldn't redeem right now.";
      // Re-enable only the packages that were affordable before.
      btns.forEach((b) => (b.disabled = b.dataset.afford !== "1"));
    }
  } catch (_e) {
    status.textContent = "Couldn't reach the guardian service.";
    btns.forEach((b) => (b.disabled = b.dataset.afford !== "1"));
  }
}

async function openAfterRedeem() {
  // Return to the page that was blocked (now within budget). Clear the SW's short-lived verdict
  // cache first so the navigation re-checks the backend instead of re-blocking on a stale verdict.
  if (!/^https?:\/\//i.test(blockedUrl || "")) {
    location.reload();
    return;
  }
  try {
    await chrome.runtime.sendMessage({
      type: "CLEAR_HOTCACHE",
      url: blockedUrl,
    });
  } catch (_e) {
    /* SW may be asleep; the navigation will still re-check time */
  }
  location.replace(blockedUrl);
}

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
initPrizeRedeem();
