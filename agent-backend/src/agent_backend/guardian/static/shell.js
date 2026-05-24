/* Aegis parent dashboard shell.
   One unlock gate holds the PIN in memory and attaches it to every data call;
   a collapsible sidebar drives hash-routed sections. All kid-controlled
   strings are rendered via textContent / value (never innerHTML) to stay
   XSS-safe — the Requests logic that moved here from the old review page
   keeps that rule. */

(() => {
  "use strict";

  const PIN_HEADER = "X-Guardian-Parent-Pin";
  let _pin = ""; // in memory only — never localStorage/sessionStorage

  const $ = (id) => document.getElementById(id);

  function el(tag, props = {}, ...children) {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(props)) {
      if (v === null || v === undefined || v === false) continue;
      if (k === "class") node.className = v;
      else if (k === "text") node.textContent = v;
      else node.setAttribute(k, v === true ? "" : v);
    }
    for (const c of children) if (c) node.append(c);
    return node;
  }

  function safeHref(url) {
    return /^https?:\/\//i.test(url || "") ? url : null;
  }

  function timeAgo(iso) {
    const t = Date.parse(iso || "");
    if (Number.isNaN(t)) return "";
    const s = Math.max(0, Math.floor((Date.now() - t) / 1000));
    if (s < 60) return s + "s ago";
    const m = Math.floor(s / 60);
    if (m < 60) return m + "m ago";
    const h = Math.floor(m / 60);
    if (h < 24) return h + "h ago";
    return Math.floor(h / 24) + "d ago";
  }

  function api(path, opts = {}) {
    const headers = Object.assign({ [PIN_HEADER]: _pin }, opts.headers || {});
    return fetch(path, Object.assign({}, opts, { headers }));
  }

  let toastTimer;
  function toast(msg) {
    const t = $("toast");
    t.textContent = msg;
    t.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => {
      t.hidden = true;
    }, 3000);
  }

  /* ---------- Sections ---------- */
  // Per-section data loaders. Filled in by later steps; the shell + routing work
  // regardless, so an unimplemented section simply shows its static markup.
  const SECTIONS = ["dashboard", "requests", "whitelist", "settings"];

  function loadDashboard() {}
  function loadWhitelist() {}

  /* Requests — pending access requests + recent decisions (moved here from the
     old /review page). Approve / reject route through POST /review/decision. */
  async function loadRequests() {
    let r;
    try {
      r = await api("/review/requests");
    } catch (_e) {
      toast("Could not reach the guardian service.");
      return;
    }
    if (r.ok) renderRequests(await r.json());
  }

  function renderRequests(data) {
    const pending = data.pending || [];
    const recent = data.recent || [];
    $("req-pending-list").replaceChildren(...pending.map(pendingCard));
    $("req-pending-empty").hidden = pending.length > 0;
    $("req-recent-list").replaceChildren(...recent.map(recentRow));
    $("req-recent-empty").hidden = recent.length > 0;
  }

  function pendingCard(req) {
    const card = el("div", { class: "card" });
    const link = safeHref(req.url)
      ? el("a", {
          href: req.url,
          target: "_blank",
          rel: "noopener noreferrer",
          class: "url",
          text: req.url,
        })
      : el("span", { class: "url", text: req.url });
    const meta = el(
      "span",
      { class: "meta" },
      req.profile
        ? el("span", { class: "badge profile", text: req.profile })
        : null,
      el("span", { class: "host", text: req.host || "" }),
    );
    card.append(el("div", { class: "head" }, link, meta));
    if (req.reason)
      card.append(el("p", { class: "reason", text: "Blocked: " + req.reason }));
    if (req.note)
      card.append(el("p", { class: "note", text: "Note: " + req.note }));
    card.append(el("p", { class: "muted", text: timeAgo(req.created_ts) }));

    card.append(
      el("label", {
        class: "entry-label",
        text: "Allow (edit to broaden — e.g. host/* or a topic like “BeyBlade anime”):",
      }),
    );
    const entry = el("input", {
      class: "entry",
      "aria-label": "Whitelist entry",
    });
    entry.value = req.url;
    card.append(entry);

    const approve = el("button", {
      class: "approve",
      type: "button",
      text: "Approve",
    });
    approve.addEventListener("click", () =>
      decide(req.id, "approve", { whitelist_entry: entry.value.trim() }),
    );

    const reject = el("button", {
      class: "reject",
      type: "button",
      text: "Reject",
    });
    const noteBox = el("textarea", {
      class: "reject-note",
      rows: "2",
      placeholder: "Optional note (why)",
      hidden: true,
    });
    const rejectConfirm = el("button", {
      class: "reject-confirm",
      type: "button",
      text: "Confirm reject",
      hidden: true,
    });
    reject.addEventListener("click", () => {
      noteBox.hidden = false;
      rejectConfirm.hidden = false;
      reject.hidden = true;
    });
    rejectConfirm.addEventListener("click", () =>
      decide(req.id, "reject", { note: noteBox.value.trim() }),
    );

    card.append(
      el("div", { class: "actions" }, approve, reject),
      noteBox,
      rejectConfirm,
    );
    return card;
  }

  function recentRow(req) {
    return el(
      "div",
      { class: "recent-row" },
      el("span", { class: "badge " + req.status, text: req.status }),
      req.profile
        ? el("span", { class: "badge profile", text: req.profile })
        : null,
      el("span", { class: "url", text: req.whitelist_entry || req.url }),
      el("span", { class: "muted", text: timeAgo(req.decided_ts) }),
    );
  }

  async function decide(id, decision, extra) {
    let r;
    try {
      r = await api("/review/decision", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(Object.assign({ id, decision }, extra)),
      });
    } catch (_e) {
      toast("Could not reach the guardian service.");
      return;
    }
    if (r.ok) {
      toast(decision === "approve" ? "Approved ✓" : "Rejected");
      loadRequests();
    } else {
      toast("Action failed (" + r.status + ").");
    }
  }

  function loadSection(key) {
    if (key === "dashboard") loadDashboard();
    else if (key === "requests") loadRequests();
    else if (key === "whitelist") loadWhitelist();
    // "settings" is a static form — nothing to fetch.
  }

  /* ---------- Hash router ---------- */
  function currentKey() {
    const raw = (location.hash || "").replace(/^#\/?/, "").split("?")[0];
    return SECTIONS.includes(raw) ? raw : "dashboard";
  }

  function route() {
    if ($("app-shell").hidden) return; // ignore hash changes before unlock
    const key = currentKey();
    for (const s of SECTIONS) {
      $("sec-" + s).hidden = s !== key;
      const nav = $("nav-" + s);
      if (s === key) nav.setAttribute("aria-current", "page");
      else nav.removeAttribute("aria-current");
    }
    $("sec-" + key).focus(); // announce the section to keyboard / screen-reader users
    loadSection(key);
    if (isMobile()) closeMobile();
  }

  /* ---------- Sidebar: desktop collapse + mobile drawer ---------- */
  const COLLAPSE_KEY = "aegis-sidebar-collapsed";

  function isMobile() {
    return window.matchMedia("(max-width: 767px)").matches;
  }

  function readCollapsed() {
    try {
      return localStorage.getItem(COLLAPSE_KEY) === "1";
    } catch (_e) {
      return false; // storage unavailable — default to expanded
    }
  }

  function writeCollapsed(on) {
    try {
      localStorage.setItem(COLLAPSE_KEY, on ? "1" : "0");
    } catch (_e) {
      /* non-critical */
    }
  }

  function applyCollapsed(on) {
    $("app-shell").classList.toggle("is-collapsed", on);
    // When collapsed the nav labels are hidden, so the control re-opens the rail.
    $("sidebar-collapse").setAttribute("aria-expanded", on ? "false" : "true");
  }

  function toggleCollapsed() {
    const on = !$("app-shell").classList.contains("is-collapsed");
    applyCollapsed(on);
    writeCollapsed(on);
  }

  function openMobile() {
    $("app-shell").classList.add("is-mobile-open");
    $("shell-toggle").setAttribute("aria-expanded", "true");
  }

  function closeMobile() {
    $("app-shell").classList.remove("is-mobile-open");
    $("shell-toggle").setAttribute("aria-expanded", "false");
  }

  function toggleMobile() {
    if ($("app-shell").classList.contains("is-mobile-open")) closeMobile();
    else openMobile();
  }

  function initSidebar() {
    applyCollapsed(readCollapsed());
    $("sidebar-collapse").addEventListener("click", toggleCollapsed);
    $("shell-toggle").addEventListener("click", toggleMobile);
    $("shell-backdrop").addEventListener("click", closeMobile);
  }

  /* ---------- Lock / unlock ---------- */
  function lock() {
    _pin = "";
    $("app-shell").hidden = true;
    $("pin-gate").hidden = false;
    $("gate-pin").value = "";
    $("gate-pin").focus();
  }

  async function unlock() {
    _pin = $("gate-pin").value.trim();
    $("gate-pin").value = ""; // don't leave the PIN sitting in the DOM
    $("gate-error").textContent = "";
    let r;
    try {
      r = await api("/review/requests");
    } catch (_e) {
      _pin = "";
      $("gate-error").textContent = "Could not reach the guardian service.";
      return;
    }
    if (r.status === 503) {
      $("gate-error").textContent =
        "No parent PIN is set up yet. Taking you to setup…";
      window.location.replace("/setup");
      return;
    }
    if (r.status === 403) {
      _pin = "";
      $("gate-error").textContent = "Wrong PIN.";
      return;
    }
    if (!r.ok) {
      _pin = "";
      $("gate-error").textContent = "Could not unlock.";
      return;
    }
    $("pin-gate").hidden = true;
    $("app-shell").hidden = false;
    if (!location.hash)
      location.hash = "#/dashboard"; // fires hashchange → route()
    else route(); // honour an existing hash (e.g. /review redirected to /#/requests)
  }

  /* ---------- Init ---------- */
  document.addEventListener("DOMContentLoaded", () => {
    $("gate-btn").addEventListener("click", unlock);
    $("gate-pin").addEventListener("keydown", (e) => {
      if (e.key === "Enter") unlock();
    });
    $("lock-btn").addEventListener("click", lock);
    initSidebar();
    window.addEventListener("hashchange", route);
    $("gate-pin").focus();
  });
})();
