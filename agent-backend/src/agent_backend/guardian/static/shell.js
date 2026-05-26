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

  // Shared with profiles.js (a separate file): the DOM/util helpers plus a slot it fills
  // with its section loader, so it reuses this PIN plumbing instead of duplicating it.
  const Aegis = (window.Aegis = window.Aegis || {});
  Aegis.$ = $;
  Aegis.el = el;
  Aegis.api = api;
  Aegis.toast = toast;

  /* ---------- Sections ---------- */
  // Per-section data loaders. Filled in by later steps; the shell + routing work
  // regardless, so an unimplemented section simply shows its static markup.
  const SECTIONS = [
    "dashboard",
    "profiles",
    "requests",
    "activity",
    "whitelist",
    "settings",
  ];

  /* Dashboard — at-a-glance counts from existing endpoints (no new aggregate
     route); each tile links into its section. */
  async function loadDashboard() {
    let pending = 0;
    let recent = 0;
    let wl = 0;
    try {
      const [rq, rw] = await Promise.all([
        api("/review/requests"),
        api("/review/whitelist"),
      ]);
      if (rq.ok) {
        const d = await rq.json();
        pending = (d.pending || []).length;
        recent = (d.recent || []).length;
      }
      if (rw.ok) wl = ((await rw.json()).entries || []).length;
    } catch (_e) {
      toast("Could not reach the guardian service.");
      return;
    }
    $("dash-tiles").replaceChildren(
      dashTile(
        pending,
        pending === 1 ? "request pending" : "requests pending",
        "#/requests",
      ),
      dashTile(
        wl,
        wl === 1 ? "whitelist entry" : "whitelist entries",
        "#/whitelist",
      ),
      dashTile(recent, "recently decided", "#/requests"),
    );
  }

  function dashTile(count, label, hash) {
    const tile = el(
      "button",
      { class: "card dash-tile", type: "button" },
      el("span", { class: "dash-tile__count", text: String(count) }),
      el("span", { class: "dash-tile__label", text: label }),
    );
    tile.addEventListener("click", () => {
      location.hash = hash;
    });
    return tile;
  }

  /* Allow & block lists — parent view via /review/whitelist + /review/blocklist, scoped to
     the profile chosen in #wl-profile (which also offers "Global — all kids"). A parent write
     must name which profile it targets. The two lists are identical except for the endpoint
     and the type badge, so one config object drives both. */
  const ALLOW = {
    endpoint: "/review/whitelist",
    list: "wl-list",
    empty: "wl-empty",
    entry: "wl-entry",
    hint: "wl-hint",
    badgeClass: "badge profile",
    verb: "allow",
  };
  const BLOCK = {
    endpoint: "/review/blocklist",
    list: "bl-list",
    empty: "bl-empty",
    entry: "bl-entry",
    hint: "bl-hint",
    badgeClass: "badge rejected",
    verb: "block",
  };

  // Shared by the Lists profile picker and the Activity filter: returns the profiles array,
  // or null on any failure (caller leaves its current options untouched).
  async function fetchProfiles() {
    try {
      const r = await api("/profiles");
      if (!r.ok) return null;
      return (await r.json()).profiles || [];
    } catch (_e) {
      return null;
    }
  }

  async function populateProfileSelect() {
    const profiles = await fetchProfiles();
    if (!profiles) return;
    const sel = $("wl-profile");
    const prev = sel.value;
    sel.replaceChildren(
      ...profiles.map((p) =>
        el("option", {
          value: p.name,
          text: p.is_global ? "Global — all kids" : p.name,
        }),
      ),
    );
    if (profiles.some((p) => p.name === prev)) sel.value = prev;
    updateActiveProfileLabel();
  }

  function updateActiveProfileLabel() {
    const sel = $("wl-profile");
    const label = $("ls-active");
    if (label)
      label.textContent =
        sel.options[sel.selectedIndex]?.text || sel.value || "—";
  }

  async function loadLists() {
    await populateProfileSelect();
    const profile = $("wl-profile").value;
    let wl, bl;
    try {
      [wl, bl] = await Promise.all([api(ALLOW.endpoint), api(BLOCK.endpoint)]);
    } catch (_e) {
      toast("Could not reach the guardian service.");
      return;
    }
    if (wl.ok) renderList(ALLOW, byProfile((await wl.json()).entries, profile));
    if (bl.ok) renderList(BLOCK, byProfile((await bl.json()).entries, profile));
    await loadPrompt();
  }

  // The selected profile's classification prompt: stored text in the textarea, the age-band
  // default as placeholder, and the effective merged guidance (Global + this profile) previewed.
  async function loadPrompt() {
    const profile = $("wl-profile").value;
    let r;
    try {
      r = await api("/review/prompt?profile=" + encodeURIComponent(profile));
    } catch (_e) {
      return;
    }
    if (!r.ok) return;
    const data = await r.json();
    $("cp-prompt").value = data.prompt || "";
    $("cp-prompt").placeholder = data.default || "";
    $("cp-merged").textContent = data.merged || "";
    $("cp-age-field").hidden = !!data.is_global;
    if (!data.is_global) $("cp-age").value = data.age == null ? "" : data.age;
    $("cp-hint").textContent =
      !data.is_global && !(data.prompt || "").trim()
        ? "No custom prompt yet — the age-appropriate default (previewed below) applies."
        : "";
  }

  async function savePrompt() {
    const profile = $("wl-profile").value;
    const body = { profile, prompt: $("cp-prompt").value };
    if (!$("cp-age-field").hidden) {
      const age = parseInt($("cp-age").value, 10);
      if (Number.isFinite(age)) body.age = age;
    }
    let r;
    try {
      r = await api("/review/prompt", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
    } catch (_e) {
      toast("Could not reach the guardian service.");
      return;
    }
    if (r.ok) {
      toast("Saved");
      loadPrompt();
    } else {
      const err = await r.json().catch(() => ({}));
      $("cp-hint").textContent = err.error || "Could not save the prompt.";
    }
  }

  // Reset = clear the stored prompt (POST empty); the age-band default then applies again.
  async function resetPrompt() {
    const profile = $("wl-profile").value;
    let r;
    try {
      r = await api("/review/prompt", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ profile, prompt: "" }),
      });
    } catch (_e) {
      toast("Could not reach the guardian service.");
      return;
    }
    if (r.ok) {
      toast("Reset to default");
      loadPrompt();
    } else {
      $("cp-hint").textContent = "Could not reset the prompt.";
    }
  }

  function byProfile(entries, profile) {
    const all = entries || [];
    return profile ? all.filter((e) => e.profile === profile) : all;
  }

  function renderList(cfg, entries) {
    $(cfg.list).replaceChildren(...entries.map((e) => listRow(cfg, e)));
    $(cfg.empty).hidden = entries.length > 0;
  }

  function listRow(cfg, entry) {
    const remove = el("button", {
      class: "reject",
      type: "button",
      text: "Remove",
    });
    remove.addEventListener("click", () => removeListEntry(cfg, entry));
    return el(
      "div",
      { class: "wl-row" },
      el("span", { class: "wl-row__value", text: entry.value }),
      el(
        "span",
        { class: "wl-row__meta" },
        el("span", { class: cfg.badgeClass, text: entry.type }),
        remove,
      ),
    );
  }

  async function addListEntry(cfg) {
    const input = $(cfg.entry);
    const value = input.value.trim();
    const hint = $(cfg.hint);
    hint.className = "hint";
    hint.textContent = "";
    if (!value) {
      hint.className = "hint bad";
      hint.textContent = "Enter a site or topic to " + cfg.verb + ".";
      return;
    }
    let r;
    try {
      r = await api(cfg.endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ entry: value, profile: $("wl-profile").value }),
      });
    } catch (_e) {
      toast("Could not reach the guardian service.");
      return;
    }
    if (r.ok) {
      input.value = "";
      toast("Added");
      loadLists();
    } else {
      hint.className = "hint bad";
      hint.textContent =
        r.status === 422
          ? "That entry isn't valid."
          : "Could not add (" + r.status + ").";
    }
  }

  async function removeListEntry(cfg, entry) {
    let r;
    try {
      r = await api(cfg.endpoint, {
        method: "DELETE",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ entry: entry.value, profile: entry.profile }),
      });
    } catch (_e) {
      toast("Could not reach the guardian service.");
      return;
    }
    if (r.ok) {
      toast("Removed");
      loadLists();
    } else {
      toast("Could not remove (" + r.status + ").");
    }
  }

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

  /* Activity — read-only timeline of recent per-URL verdicts (GET /review/activity),
     filtered by the kid chosen in #act-profile ("All profiles" = every kid). A Global-scope
     rule is attributed to the kid it affected and tagged "global rule". */
  function activityVerdict(ev) {
    if (ev.event === "cache_hit")
      return ev.verdict === "block"
        ? { label: "blocked", badge: "rejected" }
        : { label: "allowed", badge: "approved" };
    const map = {
      allow: { label: "allowed", badge: "approved" },
      whitelist_allow: { label: "allowed", badge: "approved" },
      fail_open: { label: "allowed", badge: "approved" },
      block: { label: "blocked", badge: "rejected" },
      blocklist_block: { label: "blocked", badge: "rejected" },
      escalate: { label: "checking", badge: "profile" },
    };
    return map[ev.event] || { label: ev.event, badge: "profile" };
  }

  function activityRow(ev) {
    const v = activityVerdict(ev);
    const target = ev.url || ev.url_key || "";
    const link = safeHref(target)
      ? el("a", {
          href: target,
          target: "_blank",
          rel: "noopener noreferrer",
          class: "url",
          text: target,
        })
      : el("span", { class: "url", text: target });
    return el(
      "div",
      { class: "recent-row" },
      el("span", { class: "badge " + v.badge, text: v.label }),
      ev.profile
        ? el("span", { class: "badge profile", text: ev.profile })
        : null,
      ev.scope === "global"
        ? el("span", { class: "badge", text: "global rule" })
        : null,
      link,
      el("span", { class: "muted", text: timeAgo(ev.ts) }),
    );
  }

  async function populateActivityProfiles() {
    const profiles = await fetchProfiles();
    if (!profiles) return;
    const sel = $("act-profile");
    const prev = sel.value;
    const teens = profiles.filter((p) => !p.is_global).map((p) => p.name);
    sel.replaceChildren(
      el("option", { value: "", text: "All profiles" }),
      ...teens.map((name) => el("option", { value: name, text: name })),
    );
    if (teens.includes(prev)) sel.value = prev;
  }

  async function loadActivity() {
    await populateActivityProfiles();
    const profile = $("act-profile").value;
    const qs = profile ? "?profile=" + encodeURIComponent(profile) : "";
    let r;
    try {
      r = await api("/review/activity" + qs);
    } catch (_e) {
      toast("Could not reach the guardian service.");
      return;
    }
    if (!r.ok) {
      toast("Could not load activity (" + r.status + ").");
      return;
    }
    const events = (await r.json()).events || [];
    $("act-list").replaceChildren(...events.map(activityRow));
    $("act-empty").hidden = events.length > 0;
  }

  function loadSection(key) {
    if (key === "dashboard") loadDashboard();
    else if (key === "profiles" && Aegis.loadProfiles) Aegis.loadProfiles();
    else if (key === "requests") loadRequests();
    else if (key === "activity") loadActivity();
    else if (key === "whitelist") loadLists();
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

  /* ---------- Settings: change PIN ---------- */
  function pinHint(msg, kind) {
    const h = $("set-pin-hint");
    h.className = "hint" + (kind ? " " + kind : "");
    h.textContent = msg || "";
  }

  function validatePinMatch() {
    const nw = $("set-pin-new").value.trim();
    const cf = $("set-pin-confirm").value.trim();
    if (nw && cf && nw !== cf)
      pinHint("New PIN and confirmation don't match.", "bad");
    else pinHint("", "");
  }

  async function submitChangePin() {
    const current = $("set-pin-current").value.trim();
    const nw = $("set-pin-new").value.trim();
    const cf = $("set-pin-confirm").value.trim();
    if (!/^[0-9]{4,8}$/.test(nw)) {
      pinHint("New PIN must be 4–8 digits.", "bad");
      return;
    }
    if (nw !== cf) {
      pinHint("New PIN and confirmation don't match.", "bad");
      return;
    }
    let r;
    try {
      r = await api("/settings/pin", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ current_pin: current, new_pin: nw }),
      });
    } catch (_e) {
      toast("Could not reach the guardian service.");
      return;
    }
    if (r.ok) {
      _pin = nw; // keep the unlocked session valid under the new credential
      $("set-pin-current").value = "";
      $("set-pin-new").value = "";
      $("set-pin-confirm").value = "";
      pinHint("PIN changed.", "ok");
      toast("PIN changed ✓");
      return;
    }
    if (r.status === 403) {
      pinHint("Current PIN is incorrect.", "bad");
    } else if (r.status === 400) {
      let msg = "New PIN is not valid.";
      try {
        msg = (await r.json()).error || msg;
      } catch (_e) {
        /* keep default message */
      }
      pinHint(msg, "bad");
    } else {
      pinHint("Could not change PIN (" + r.status + ").", "bad");
    }
  }

  /* ---------- Init ---------- */
  document.addEventListener("DOMContentLoaded", () => {
    $("gate-btn").addEventListener("click", unlock);
    $("gate-pin").addEventListener("keydown", (e) => {
      if (e.key === "Enter") unlock();
    });
    $("lock-btn").addEventListener("click", lock);
    $("wl-add-btn").addEventListener("click", () => addListEntry(ALLOW));
    $("wl-entry").addEventListener("keydown", (e) => {
      if (e.key === "Enter") addListEntry(ALLOW);
    });
    $("bl-add-btn").addEventListener("click", () => addListEntry(BLOCK));
    $("bl-entry").addEventListener("keydown", (e) => {
      if (e.key === "Enter") addListEntry(BLOCK);
    });
    $("wl-profile").addEventListener("change", loadLists);
    $("cp-save").addEventListener("click", savePrompt);
    $("cp-reset").addEventListener("click", resetPrompt);
    $("act-profile").addEventListener("change", loadActivity);
    $("act-refresh").addEventListener("click", loadActivity);
    $("set-pin-btn").addEventListener("click", submitChangePin);
    $("set-pin-new").addEventListener("input", validatePinMatch);
    $("set-pin-confirm").addEventListener("input", validatePinMatch);
    initSidebar();
    window.addEventListener("hashchange", route);
    $("gate-pin").focus();
  });
})();
