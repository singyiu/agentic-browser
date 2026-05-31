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

  // Redux store for pending-request state (created in store.js, loaded before this file).
  // Guarded: if the vendored Redux failed to load, AS is null and the badge/polling no-op
  // while the rest of the dashboard keeps working.
  const AS = window.AegisStore || null;
  const POLL_MS = 15000; // pending-request poll cadence; paused while the tab is hidden
  let _pollTimer = null;
  let _forceRender = false; // set by decide() so a resolved card leaves even with its note open
  let _wlCount = 0; // last-known whitelist size, for the dashboard tile across store re-renders

  // Activity-page rule builder (pure logic in rules.js, loaded before this file). If it failed to
  // load, AR is null and the Activity page stays read-only — the +Rule / Suggest-rules affordances
  // simply don't appear, the rest of the dashboard is unaffected.
  const AR = window.AegisRules || null;
  // Collapses a same-URL burst (escalate -> checking, then the terminal allow/block) into one
  // row, display-only. Absent -> the raw, un-collapsed feed renders (graceful degrade).
  const AA = window.AegisActivity || null;
  let _openRuleBuilder = null; // the single inline builder currently expanded (one open at a time)
  let _openRuleTrigger = null; // the button that opened it, so its aria-expanded resets on close
  let _ruleBuilderSeq = 0; // unique radio-group names so multiple builders never collide
  let _actProfiles = []; // profiles cached from the last load, for the builder's checkboxes
  // Activity-summary feature (dashboard panel + Summaries tab). ASUM does the safe shaping;
  // absent -> the panel hides and the tab no-ops (graceful degrade, like AR/AA above).
  const ASUM = window.AegisSummary || null;
  let _summaryAutoTried = false; // auto-generate at most once per page session when stale
  let _summaryRefreshing = false; // re-entrancy guard for the Refresh button + auto-gen
  const ATIME = window.AegisTime || null; // pure time shaping/formatting (time-core.js)
  let _timePending = 0; // pending time-extension requests, folded into the sidebar badge
  let _actSummaries = []; // normalized history, cached for instant profile-filter re-render

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
    "time",
    "settings",
  ];

  /* Dashboard — at-a-glance counts. Pending/recent come from the Redux store (kept fresh by
     polling) so the tiles update live; the whitelist count keeps its own fetch. Falls back to
     a direct count when Redux is unavailable, so the dashboard never regresses. */
  async function loadDashboard() {
    try {
      if (AS) {
        const rw = await api("/review/whitelist");
        if (rw.ok) _wlCount = ((await rw.json()).entries || []).length;
        refreshRequests({ silent: true }); // store update -> renderFromStore re-renders the tiles
        renderDashboardCounts(); // instant paint from whatever the store already holds
      } else {
        const [rq, rw] = await Promise.all([
          api("/review/requests"),
          api("/review/whitelist"),
        ]);
        let pending = 0;
        let recent = 0;
        if (rq.ok) {
          const d = await rq.json();
          pending = (d.pending || []).length;
          recent = (d.recent || []).length;
        }
        if (rw.ok) _wlCount = ((await rw.json()).entries || []).length;
        renderDashboardTiles(pending, recent, _wlCount);
      }
    } catch (_e) {
      toast("Could not reach the guardian service.");
    }
    loadActivitySummary(); // fire-and-forget; the AI panel has its own loading + error handling
    loadScreenTimeChart(); // fire-and-forget embed of the Grafana per-profile screen-time chart
  }

  // Re-render the dashboard tiles from current store state (called by the store subscriber).
  function renderDashboardCounts() {
    if (!AS || $("sec-dashboard").hidden) return;
    const s = AS.store.getState();
    renderDashboardTiles(
      AS.selectors.pendingCount(s),
      AS.selectors.recent(s).length,
      _wlCount,
    );
  }

  function renderDashboardTiles(pending, recent, wl) {
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

  /* ---------- Screen time (embedded Grafana panels) ---------- */
  /* The dashboard line chart (#2) and the Activity "Screen time" tab (#3) embed provisioned
     panels from the local observability stack's "guardian-browser-usage" Grafana dashboard via
     Grafana's d-solo render. The data + charting live in Grafana; we only point an <iframe> at
     it, so there's no PIN-gated fetch here (Grafana serves the panels on loopback). */
  const GRAFANA_BASE = "http://localhost:3000"; // observability/docker-compose.yml (GRAFANA_PORT)
  const GRAFANA_DASH_UID = "guardian-browser-usage";

  // Build a Grafana solo-panel URL for an <iframe>. `from`/`to` take Grafana relative ranges
  // (e.g. "now-48h"); `profile`, when set, selects the dashboard's `profile` template variable.
  function soloUrl(panelId, { profile, from = "now-14d", to = "now" } = {}) {
    const p = new URLSearchParams({
      orgId: "1",
      panelId: String(panelId),
      theme: "light",
      from,
      to,
    });
    if (profile) p.set("var-profile", profile);
    return (
      GRAFANA_BASE +
      "/d-solo/" +
      GRAFANA_DASH_UID +
      "/screen-time?" +
      p.toString()
    );
  }

  // Dashboard line chart (#2): total screen time per day, one series per profile. Loaded once per
  // page session (Grafana self-refreshes), so re-routing to the dashboard doesn't reload it.
  function loadScreenTimeChart() {
    const frame = $("dash-st-frame");
    if (!frame || frame.dataset.loaded) return;
    frame.src = soloUrl(11, { from: "now-14d", to: "now" });
    frame.dataset.loaded = "1";
  }

  /* ---------- Dashboard: AI activity summary ---------- */
  /* An AI-written per-profile review of recent activity. GET shows the saved summary; if it's
     older than 48h (or missing) and there's activity, we auto-generate once per session. Refresh
     regenerates on demand. summary.js (ASUM) does the safe shaping — this is DOM glue only. */
  async function loadActivitySummary() {
    if (!ASUM) {
      $("dash-summary").hidden = true;
      return;
    }
    let r;
    try {
      r = await api("/review/activity/summary");
    } catch (_e) {
      $("dash-summary-meta").textContent =
        "Couldn't reach the guardian service.";
      return;
    }
    if (!r.ok) {
      $("dash-summary-meta").textContent = "Couldn't load the summary.";
      return;
    }
    const norm = ASUM.normalizeSummary(await r.json());
    renderDashboardSummary(norm);
    if (norm.stale && norm.has_activity && !_summaryAutoTried) {
      _summaryAutoTried = true; // auto-generate at most once per page session
      refreshActivitySummary({ auto: true });
    }
  }

  function renderDashboardSummary(norm) {
    const meta = $("dash-summary-meta");
    const body = $("dash-summary-body");
    const updated = norm.generated_at
      ? "Updated " + timeAgo(norm.generated_at)
      : "";
    if (!norm.has_activity) {
      meta.textContent = "";
      body.replaceChildren(
        el("p", {
          class: "muted",
          text: "No recent activity to summarize yet.",
        }),
      );
      return;
    }
    if (ASUM.summaryIsEmpty(norm)) {
      meta.textContent = updated;
      body.replaceChildren(
        el("p", {
          class: "muted",
          text: norm.generated_at
            ? "The last review found nothing notable."
            : "No summary yet — generating one now…",
        }),
      );
      return;
    }
    meta.textContent = updated;
    body.replaceChildren(...norm.profiles.map(summaryProfileCard));
  }

  // One per-profile summary card (overview + optional Trends and "Worth a look" lists). Reused by
  // the dashboard panel and the Activity "Summaries" tab. Every string goes through el({text}) so
  // AI/teen content renders inert (no HTML injection).
  function summaryProfileCard(p) {
    const parts = [
      el(
        "div",
        { class: "summary-profile__head" },
        el("span", { class: "badge profile", text: p.profile }),
      ),
    ];
    if (p.summary)
      parts.push(el("p", { class: "summary-profile__text", text: p.summary }));
    if (p.trends && p.trends.length)
      parts.push(summarySection("Trends", p.trends));
    if (p.attention && p.attention.length)
      parts.push(summarySection("Worth a look", p.attention, "attention"));
    return el("div", { class: "card summary-profile" }, ...parts);
  }

  function summarySection(label, items, modifier) {
    return el(
      "div",
      {
        class:
          "summary-section" + (modifier ? " summary-section--" + modifier : ""),
      },
      el("p", { class: "summary-section__label", text: label }),
      el(
        "ul",
        { class: "summary-section__list" },
        ...items.map((t) => el("li", { text: t })),
      ),
    );
  }

  async function refreshActivitySummary(opts) {
    const auto = !!(opts && opts.auto);
    if (!ASUM || _summaryRefreshing) return;
    _summaryRefreshing = true;
    const btn = $("dash-summary-refresh");
    const orig = btn.textContent;
    btn.disabled = true;
    btn.textContent = "Refreshing…";
    if (auto) $("dash-summary-meta").textContent = "Generating…";
    let r;
    try {
      r = await api("/review/activity/summary", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: "{}",
      });
    } catch (_e) {
      toast("Could not reach the guardian service.");
      _summaryRefreshing = false;
      btn.disabled = false;
      btn.textContent = orig;
      return;
    }
    _summaryRefreshing = false;
    btn.disabled = false;
    btn.textContent = orig;
    if (!r.ok) {
      toast("Could not generate summary (" + r.status + ").");
      return;
    }
    renderDashboardSummary(ASUM.normalizeSummary(await r.json()));
    if (!auto) toast("Summary updated.");
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
  const SK_ALLOW = {
    endpoint: "/review/search-keywords/allow",
    list: "sk-list",
    empty: "sk-empty",
    entry: "sk-entry",
    hint: "sk-hint",
    badgeClass: "badge profile",
    badgeText: "keyword",
    noun: "search keyword",
    verb: "allow",
  };
  const SK_BLOCK = {
    endpoint: "/review/search-keywords/block",
    list: "skb-list",
    empty: "skb-empty",
    entry: "skb-entry",
    hint: "skb-hint",
    badgeClass: "badge rejected",
    badgeText: "keyword",
    noun: "search keyword",
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
    await loadSearchKeywords();
  }

  // The selected profile's search-keyword allow/block lists (parent-managed under Guard).
  async function loadSearchKeywords() {
    const profile = $("wl-profile").value;
    let ska, skb;
    try {
      [ska, skb] = await Promise.all([
        api(SK_ALLOW.endpoint),
        api(SK_BLOCK.endpoint),
      ]);
    } catch (_e) {
      return;
    }
    if (ska.ok)
      renderList(SK_ALLOW, byProfile((await ska.json()).entries, profile));
    if (skb.ok)
      renderList(SK_BLOCK, byProfile((await skb.json()).entries, profile));
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
        el("span", {
          class: cfg.badgeClass,
          text: entry.type || cfg.badgeText || "",
        }),
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
      hint.textContent =
        "Enter a " + (cfg.noun || "site or topic") + " to " + cfg.verb + ".";
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

  /* Requests — pending access requests + recent decisions (moved here from the old /review
     page). Approve / reject route through POST /review/decision. The list is rendered from the
     Redux store by renderFromStore (subscribed in init); fetching dispatches into the store. */
  function loadRequests() {
    if (AS) renderFromStore(); // instant paint from the latest poll, then refresh
    refreshRequests();
    refreshTimeRequests();
  }

  // Single fetch path for the requests slice. Background polls and the post-decision refresh pass
  // {silent:true} so a transient failure doesn't spam toasts; foreground loads surface it.
  async function refreshRequests({ silent = false } = {}) {
    if (!AS) {
      // No store (Redux failed to load) — render directly so Requests still works.
      try {
        const r = await api("/review/requests");
        if (r.ok) renderRequests(await r.json());
        else if (!silent) toast("Could not load requests (" + r.status + ").");
      } catch (_e) {
        if (!silent) toast("Could not reach the guardian service.");
      }
      return;
    }
    AS.store.dispatch(AS.actions.requestsLoading());
    let r;
    try {
      r = await api("/review/requests");
    } catch (_e) {
      AS.store.dispatch(AS.actions.requestsFailed("network", Date.now()));
      if (!silent) toast("Could not reach the guardian service.");
      return;
    }
    if (!r.ok) {
      AS.store.dispatch(
        AS.actions.requestsFailed(String(r.status), Date.now()),
      );
      if (!silent && r.status !== 403)
        toast("Could not load requests (" + r.status + ").");
      return;
    }
    const d = await r.json();
    AS.store.dispatch(
      AS.actions.requestsLoaded(d.pending || [], d.recent || [], Date.now()),
    );
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
    const isSearch = req.kind === "search";
    const head = isSearch
      ? el("span", { class: "url", text: "Search: " + (req.keyword || "") })
      : safeHref(req.url)
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
      isSearch ? null : el("span", { class: "host", text: req.host || "" }),
    );
    card.append(el("div", { class: "head" }, head, meta));
    if (req.reason)
      card.append(el("p", { class: "reason", text: "Blocked: " + req.reason }));
    if (req.note)
      card.append(el("p", { class: "note", text: "Note: " + req.note }));
    card.append(el("p", { class: "muted", text: timeAgo(req.created_ts) }));

    const approve = el("button", {
      class: "approve",
      type: "button",
      text: "Approve",
    });
    if (isSearch) {
      // Approving a search request adds the fixed keyword to the teen's search allow list.
      approve.addEventListener("click", () => decide(req.id, "approve", {}));
    } else {
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
      approve.addEventListener("click", () =>
        decide(req.id, "approve", { whitelist_entry: entry.value.trim() }),
      );
    }

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
    // Optional "block similar content" workflow: the guardian may ask the AI to draft a
    // natural-language rule, edit it, choose scope, then confirm. Skipping it leaves reject
    // exactly as before.
    const suggestBtn = el("button", {
      class: "ghost suggest-rule",
      type: "button",
      text: "✨ Suggest a rule for similar content",
      hidden: true,
    });
    const ruleBox = el("textarea", {
      class: "rule-box",
      rows: "2",
      "aria-label": "Blocking rule for similar content",
    });
    const hardChk = el("input", { type: "checkbox", checked: true });
    const hardRow = el(
      "label",
      { class: "rule-hard" },
      hardChk,
      el("span", {
        text: isSearch
          ? ' Also block this search term ("' +
            (req.keyword || "") +
            '") instantly'
          : " Also block this site (" + (req.host || "") + ") instantly",
      }),
    );
    const scopeProfile = el("input", {
      type: "radio",
      name: "scope-" + req.id,
      value: "profile",
      checked: true,
    });
    const scopeGlobal = el("input", {
      type: "radio",
      name: "scope-" + req.id,
      value: "global",
    });
    const scopeRow = el(
      "div",
      { class: "rule-scope" },
      el("span", { class: "muted", text: "Apply to:" }),
      el("label", {}, scopeProfile, el("span", { text: " This child" })),
      el("label", {}, scopeGlobal, el("span", { text: " All children" })),
    );
    const dismissBtn = el("button", {
      class: "ghost rule-dismiss",
      type: "button",
      text: "Dismiss",
    });
    const ruleWrap = el(
      "div",
      { class: "rule-wrap", hidden: true },
      el("label", {
        class: "entry-label",
        text: "AI-suggested rule (edit, or clear the box to skip):",
      }),
      ruleBox,
      hardRow,
      scopeRow,
      dismissBtn,
    );

    reject.addEventListener("click", () => {
      noteBox.hidden = false;
      rejectConfirm.hidden = false;
      suggestBtn.hidden = false;
      reject.hidden = true;
    });

    suggestBtn.addEventListener("click", async () => {
      suggestBtn.disabled = true;
      suggestBtn.textContent = "Generating…";
      let r;
      try {
        r = await api("/review/suggest-block-rule", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ id: req.id }),
        });
      } catch (_e) {
        toast("Could not reach the guardian service.");
        suggestBtn.disabled = false;
        suggestBtn.textContent = "✨ Suggest a rule for similar content";
        return;
      }
      if (r.ok) {
        const data = await r.json();
        ruleBox.value = data.rule || "";
        ruleWrap.hidden = false;
        suggestBtn.hidden = true;
      } else {
        toast("Could not generate a rule (" + r.status + ").");
        suggestBtn.disabled = false;
        suggestBtn.textContent = "✨ Suggest a rule for similar content";
      }
    });

    dismissBtn.addEventListener("click", () => {
      ruleWrap.hidden = true;
      ruleBox.value = "";
      suggestBtn.hidden = false;
      suggestBtn.disabled = false;
      suggestBtn.textContent = "✨ Suggest a rule for similar content";
    });

    rejectConfirm.addEventListener("click", () => {
      const extra = { note: noteBox.value.trim() };
      if (!ruleWrap.hidden) {
        const rule = ruleBox.value.trim();
        if (rule) extra.block_rule = rule;
        extra.block_hard = hardChk.checked;
        extra.block_scope = scopeGlobal.checked ? "global" : "profile";
      }
      decide(req.id, "reject", extra);
    });

    card.append(
      el("div", { class: "actions" }, approve, reject),
      noteBox,
      suggestBtn,
      ruleWrap,
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
      el("span", {
        class: "url",
        text: req.keyword || req.whitelist_entry || req.url,
      }),
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
      if (decision === "approve") {
        toast("Approved ✓");
      } else {
        let suffix = "";
        try {
          const data = await r.json();
          if (data.rule_applied || data.hard_block_applied)
            suffix = " · rule added";
        } catch (_e) {
          // No body / parse error — fall back to the plain reject toast.
        }
        toast("Rejected" + suffix);
      }
      _forceRender = true; // remove the resolved card even though its reject note is open
      refreshRequests({ silent: true });
    } else {
      toast("Action failed (" + r.status + ").");
    }
  }

  /* ---------- Time-extension requests ---------- */
  // Fetched alongside the access/search queue (own endpoint, not in the Redux store). Pending
  // count folds into the sidebar badge so a backgrounded parent still notices.
  async function refreshTimeRequests({ silent = false } = {}) {
    let r;
    try {
      r = await api("/review/time-requests");
    } catch (_e) {
      if (!silent) toast("Could not reach the guardian service.");
      return;
    }
    if (!r.ok) {
      if (!silent && r.status !== 403)
        toast("Could not load time requests (" + r.status + ").");
      return;
    }
    const data = await r.json();
    const pending = ATIME
      ? ATIME.normalizeTimeRequests(data.pending || [])
      : data.pending || [];
    _timePending = pending.length;
    const wrap = $("req-time");
    if (wrap) wrap.hidden = pending.length === 0;
    const list = $("req-time-list");
    if (list) list.replaceChildren(...pending.map(timeRequestCard));
    updateBadge(pendingTotal());
  }

  function timeRequestCard(req) {
    const card = el("div", { class: "card" });
    const meta = el(
      "span",
      { class: "meta" },
      req.profile
        ? el("span", { class: "badge profile", text: req.profile })
        : null,
      req.target_host
        ? el("span", { class: "host", text: req.target_host })
        : null,
    );
    const ask = req.requested_minutes
      ? "Wants +" + req.requested_minutes + " min"
      : "Wants more time";
    card.append(
      el(
        "div",
        { class: "head" },
        el("span", { class: "url", text: ask }),
        meta,
      ),
    );
    if (req.reason)
      card.append(el("p", { class: "reason", text: "Reason: " + req.reason }));
    if (req.note)
      card.append(el("p", { class: "note", text: "Note: " + req.note }));
    card.append(el("p", { class: "muted", text: timeAgo(req.created_ts) }));

    const grants = el("div", { class: "time-req-grants" });
    for (const m of [15, 30, 60]) {
      const b = el("button", {
        class: "approve",
        type: "button",
        text: "+" + m + "m",
      });
      b.addEventListener("click", () =>
        decideTime(req.id, req.profile, "approve", m),
      );
      grants.append(b);
    }
    const custom = el("input", {
      type: "number",
      min: "1",
      max: "1440",
      class: "time-req-custom",
      "aria-label": "Custom minutes to grant",
      placeholder: "min",
    });
    const customBtn = el("button", {
      class: "ghost",
      type: "button",
      text: "Grant",
    });
    customBtn.addEventListener("click", () => {
      const n = Math.round(Number(custom.value));
      if (!Number.isFinite(n) || n < 1) {
        toast("Enter how many minutes to grant.");
        return;
      }
      decideTime(req.id, req.profile, "approve", Math.min(1440, n));
    });
    const deny = el("button", {
      class: "reject",
      type: "button",
      text: "Deny",
    });
    deny.addEventListener("click", () =>
      decideTime(req.id, req.profile, "reject", null),
    );
    grants.append(custom, customBtn, deny);
    card.append(grants);
    return card;
  }

  async function decideTime(id, profile, decision, grantedMinutes) {
    const body = { id, profile, decision };
    if (decision === "approve") body.granted_minutes = grantedMinutes;
    let r;
    try {
      r = await api("/review/time-decision", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
    } catch (_e) {
      toast("Could not reach the guardian service.");
      return;
    }
    if (r.ok) {
      toast(
        decision === "approve"
          ? "Granted +" + grantedMinutes + " min ✓"
          : "Denied",
      );
      refreshTimeRequests({ silent: true });
    } else {
      toast("Action failed (" + r.status + ").");
    }
  }

  // Sidebar badge total = pending access/search requests (store) + pending time requests.
  function pendingTotal() {
    let base = 0;
    if (AS) {
      try {
        base = AS.selectors.pendingCount(AS.store.getState()) || 0;
      } catch (_e) {
        base = 0;
      }
    }
    return base + _timePending;
  }

  /* ---------- Pending-request badge + polling (Redux-driven) ---------- */
  // Subscribed to the store: refresh the sidebar count badge on every change, and re-render the
  // open Requests/Dashboard lists from store state. A poll never clobbers an in-progress review
  // (typed note / AI rule); decide() sets _forceRender to flush the resolved card.
  function renderFromStore() {
    if (!AS) return;
    const s = AS.store.getState();
    updateBadge(pendingTotal());
    // Only paint lists from settled, fresh data. On "loading"/"error" keep the last-good DOM
    // and, crucially, preserve _forceRender so a decision still flushes once a refresh succeeds
    // (otherwise a failed post-decision poll would strand the resolved card on screen).
    if (AS.selectors.status(s) !== "ready") return;
    if (!$("sec-requests").hidden && (_forceRender || !isReviewing())) {
      renderRequests({
        pending: AS.selectors.pending(s),
        recent: AS.selectors.recent(s),
      });
      _forceRender = false;
    }
    renderDashboardCounts();
  }

  // True while a card's reject note or AI-rule box is open, so polling won't wipe it.
  function isReviewing() {
    const list = $("req-pending-list");
    return (
      !!list &&
      !!list.querySelector(
        ".reject-note:not([hidden]), .rule-wrap:not([hidden])",
      )
    );
  }

  // The sidebar count badge (and the tab title, so a backgrounded tab still notifies the parent).
  function updateBadge(n) {
    const badge = $("nav-requests-badge");
    if (!badge) return;
    const label = n > 99 ? "99+" : String(n);
    badge.textContent = label;
    badge.hidden = n <= 0;
    $("nav-requests").setAttribute(
      "aria-label",
      n > 0 ? "Requests, " + n + " pending" : "Requests",
    );
    document.title = n > 0 ? "(" + label + ") Aegis" : "Aegis";
  }

  function pollTick() {
    if (document.hidden) return; // paused while the tab is backgrounded
    refreshRequests({ silent: true });
    refreshTimeRequests({ silent: true });
  }

  function startPolling({ immediate = true } = {}) {
    stopPolling();
    if (immediate) pollTick();
    _pollTimer = setInterval(pollTick, POLL_MS);
  }

  function stopPolling() {
    if (_pollTimer) {
      clearInterval(_pollTimer);
      _pollTimer = null;
    }
  }

  // Refresh immediately when the parent returns to the tab (polling was paused while hidden).
  function onVisibility() {
    if (!document.hidden && !$("app-shell").hidden) {
      refreshRequests({ silent: true });
      refreshTimeRequests({ silent: true });
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
          title: target, // full URL on hover; the visible text is clipped with an ellipsis
          text: target,
        })
      : el("span", { class: "url", title: target, text: target });
    const row = el(
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
      // When this row stands in for a collapsed same-URL burst, say so (transparency: the parent
      // sees fewer rows than raw events). Singletons (_count 1 / undefined) show nothing.
      ev._count > 1
        ? el("span", {
            class: "muted act-count",
            text: ev._count + " events",
            title:
              ev._count + " activity events around this time merged into one",
            "aria-label":
              ev._count + " activity events around this time merged into one",
          })
        : null,
    );
    // Without rules.js the page stays read-only (graceful degrade — no +Rule affordance).
    if (!AR) return row;
    const item = el("div", { class: "activity-item" }, row);
    const addBtn = el("button", {
      class: "ghost add-rule",
      type: "button",
      text: "+ Rule",
      "aria-label": "Create a blocking rule from this item",
      "aria-expanded": "false",
    });
    addBtn.addEventListener("click", () =>
      toggleRuleBuilder(item, { seedEv: ev, seedKind: "exact" }, addBtn),
    );
    row.append(addBtn);
    return item;
  }

  // One POST to the existing per-profile blocklist endpoint; never throws (the caller tallies
  // ok/fail per profile so a partial multi-profile apply can name the profiles that failed).
  async function applyOneRule(payload) {
    try {
      const r = await api("/review/blocklist", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      return { profile: payload.profile, ok: r.ok };
    } catch (_e) {
      return { profile: payload.profile, ok: false };
    }
  }

  function closeRuleBuilder() {
    if (_openRuleBuilder) {
      _openRuleBuilder.remove();
      _openRuleBuilder = null;
    }
    if (_openRuleTrigger) {
      _openRuleTrigger.setAttribute("aria-expanded", "false");
      _openRuleTrigger = null;
    }
  }

  // Open the inline builder inside `container` (an activity item or a suggestion card), closing
  // any other open one first — only a single builder is ever expanded. A second click on the same
  // trigger closes it (toggle).
  function toggleRuleBuilder(container, opts, trigger) {
    const openHere =
      _openRuleBuilder && _openRuleBuilder.parentNode === container;
    closeRuleBuilder();
    if (openHere) return;
    const wrap = buildRuleBuilder(opts);
    container.append(wrap);
    _openRuleBuilder = wrap;
    _openRuleTrigger = trigger || null;
    if (trigger) trigger.setAttribute("aria-expanded", "true");
  }

  // The reusable inline rule builder. `seedEv` is the source activity item (null for an AI
  // suggestion); passing `seedValue` (a string) marks a suggestion-sourced builder whose text must
  // not be auto-rewritten when the kind changes.
  function buildRuleBuilder(opts) {
    const seedEv = opts.seedEv || null;
    const fromSuggestion = typeof opts.seedValue === "string";
    const allowed = ["exact", "wildcard", "nl", "ai"];
    let kind = allowed.includes(opts.seedKind) ? opts.seedKind : "nl";
    let lastSeed = "";

    const box = el("textarea", { class: "rule-box", rows: "2" });
    if (fromSuggestion) {
      box.value = opts.seedValue;
    } else {
      lastSeed = AR.seedValue(seedEv, kind);
      box.value = lastSeed;
    }

    const hint = el("span", { class: "rule-hint error", hidden: true });
    const setHint = (msg, show) => {
      hint.textContent = msg || "";
      hint.hidden = !show;
    };

    const genBtn = el("button", {
      class: "ghost suggest-rule",
      type: "button",
      text: "✨ Generate suggestion",
      hidden: kind !== "ai",
    });
    genBtn.addEventListener("click", async () => {
      const url = (seedEv && (seedEv.url || seedEv.url_key)) || "";
      if (!url) {
        setHint("No link on this item to base a suggestion on.", true);
        return;
      }
      genBtn.disabled = true;
      const orig = genBtn.textContent;
      genBtn.textContent = "Generating…";
      let r;
      try {
        r = await api("/review/activity/suggest-rule", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            url,
            profile: seedEv.profile || "",
            event: seedEv.event || "",
          }),
        });
      } catch (_e) {
        toast("Could not reach the guardian service.");
        genBtn.disabled = false;
        genBtn.textContent = orig;
        return;
      }
      if (r.ok) {
        const d = await r.json();
        box.value = d.rule || "";
        setHint("", false);
      } else {
        toast("Could not generate a rule (" + r.status + ").");
      }
      genBtn.disabled = false;
      genBtn.textContent = orig;
    });

    const radioName = "rule-kind-" + _ruleBuilderSeq++;
    const radios = AR.KINDS.map((k) => {
      const input = el("input", {
        type: "radio",
        name: radioName,
        value: k.id,
      });
      input.checked = k.id === kind;
      input.addEventListener("change", () => {
        if (!input.checked) return;
        kind = k.id;
        genBtn.hidden = kind !== "ai";
        // Re-derive the box only for an activity-sourced builder the user hasn't edited.
        if (
          !fromSuggestion &&
          (box.value.trim() === "" || box.value === lastSeed)
        ) {
          lastSeed = AR.seedValue(seedEv, kind);
          box.value = lastSeed;
        }
      });
      return el(
        "label",
        { class: "rule-kind" },
        input,
        el("span", { text: " " + k.label }),
      );
    });

    const profileWrap = el("div", { class: "rule-profiles" });
    const checks = [];
    for (const p of _actProfiles.filter((x) => !x.is_global)) {
      const cb = el("input", { type: "checkbox", value: p.name });
      if (seedEv && seedEv.profile === p.name) cb.checked = true;
      checks.push(cb);
      profileWrap.append(
        el(
          "label",
          { class: "rule-profile" },
          cb,
          el("span", { text: " " + p.name }),
        ),
      );
    }
    const globalCb = el("input", { type: "checkbox", value: "global" });
    checks.push(globalCb);
    profileWrap.append(
      el(
        "label",
        { class: "rule-profile" },
        globalCb,
        el("span", { text: " Global — all kids" }),
      ),
    );

    const applyBtn = el("button", {
      class: "primary apply-rule",
      type: "button",
      text: "Apply rule",
    });
    const dismissBtn = el("button", {
      class: "ghost rule-dismiss",
      type: "button",
      text: "Dismiss",
    });
    dismissBtn.addEventListener("click", closeRuleBuilder);
    applyBtn.addEventListener("click", async () => {
      const selected = checks.filter((c) => c.checked).map((c) => c.value);
      const payloads = AR.buildApplyPayloads(box.value, selected);
      if (payloads.length === 0) {
        const norm = AR.normalizeEntry(box.value);
        setHint(norm.ok ? "Pick at least one profile." : norm.error, true);
        return;
      }
      setHint("", false);
      applyBtn.disabled = true;
      const orig = applyBtn.textContent;
      applyBtn.textContent = "Applying…";
      const results = await Promise.all(payloads.map(applyOneRule));
      applyBtn.disabled = false;
      applyBtn.textContent = orig;
      const okCount = results.filter((r) => r.ok).length;
      const failed = results.filter((r) => !r.ok).map((r) => r.profile);
      if (okCount) {
        toast(
          "Rule added to " +
            okCount +
            " profile" +
            (okCount > 1 ? "s" : "") +
            ".",
        );
      }
      if (failed.length)
        setHint("Could not apply for: " + failed.join(", ") + ".", true);
      else closeRuleBuilder();
    });

    return el(
      "div",
      { class: "rule-wrap" },
      el("label", { class: "entry-label", text: "New blocking rule" }),
      el("div", { class: "rule-kinds" }, ...radios),
      box,
      genBtn,
      el("label", { class: "entry-label", text: "Apply to:" }),
      profileWrap,
      hint,
      el("div", { class: "rule-actions" }, applyBtn, dismissBtn),
    );
  }

  // "Suggest rules": ask the AI to review recent activity + existing rules, then list its proposals
  // as cards. Each card's "Use this rule" opens the builder pre-filled — the AI never writes a rule
  // on its own (the guardian confirms via Apply).
  async function suggestActivityRules() {
    if (!AR) return;
    const btn = $("act-suggest");
    btn.disabled = true;
    const orig = btn.textContent;
    btn.textContent = "Reviewing…";
    const profile = $("act-profile").value;
    let r;
    try {
      r = await api("/review/activity/suggest-rules", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(profile ? { profile } : {}),
      });
    } catch (_e) {
      toast("Could not reach the guardian service.");
      btn.disabled = false;
      btn.textContent = orig;
      return;
    }
    btn.disabled = false;
    btn.textContent = orig;
    if (!r.ok) {
      toast("Could not get suggestions (" + r.status + ").");
      return;
    }
    const data = await r.json();
    renderSuggestions(AR.normalizeSuggestions(data.suggestions || []));
  }

  function renderSuggestions(suggestions) {
    const panel = $("act-suggestions");
    if (!suggestions.length) {
      // The AI ran but proposed nothing new. A 3s toast was too easy to miss (it read as
      // "nothing happened"), so render a standing card right where suggestions would appear.
      // role=status announces it to screen readers when it replaces the panel contents.
      panel.replaceChildren(
        el(
          "div",
          { class: "card suggestions-empty", role: "status" },
          el("p", {
            class: "suggestions-empty-title",
            text: "No new rules to suggest",
          }),
          el("p", {
            class: "muted suggestions-empty-body",
            text: "Your existing rules already cover what's in recent activity. Try again after more browsing, or use + Rule on any item to add a rule yourself.",
          }),
        ),
      );
      return;
    }
    panel.replaceChildren(
      el("h2", { class: "suggestions-title", text: "Suggested rules" }),
      ...suggestions.map(suggestionCard),
    );
  }

  function suggestionCard(s) {
    const card = el("div", { class: "card suggestion" });
    const useBtn = el("button", {
      class: "ghost use-rule",
      type: "button",
      text: "Use this rule",
      "aria-expanded": "false",
    });
    useBtn.addEventListener("click", () =>
      toggleRuleBuilder(
        card,
        { seedEv: null, seedKind: s.kind, seedValue: s.value },
        useBtn,
      ),
    );
    card.append(
      el(
        "div",
        { class: "suggestion-head" },
        el("span", { class: "badge profile", text: s.kind }),
        el("code", { class: "suggestion-value", text: s.value }),
      ),
      s.reason
        ? el("p", { class: "muted suggestion-reason", text: s.reason })
        : null,
      useBtn,
    );
    return card;
  }

  async function populateActivityProfiles() {
    const profiles = await fetchProfiles();
    if (!profiles) return;
    _actProfiles = profiles; // cached for the rule-builder's per-profile checkboxes
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
    closeRuleBuilder(); // a refresh / profile-change replaces the list; drop any open builder
    if (AR) $("act-suggestions").replaceChildren();
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
    // Collapse same-URL bursts (checking -> allowed/blocked) into one row each; the raw audit
    // log is untouched. Without activity.js the un-collapsed feed renders unchanged.
    const rows = AA ? AA.consolidate(events) : events;
    $("act-list").replaceChildren(...rows.map(activityRow));
    $("act-empty").hidden = events.length > 0;
  }

  /* ---------- Activity tabs: Timeline | Summaries ---------- */
  function setActivityTab(name) {
    const timeline = name !== "summaries";
    $("act-tab-timeline").hidden = !timeline;
    $("act-tab-summaries").hidden = timeline;
    $("act-tab-timeline-btn").setAttribute("aria-selected", String(timeline));
    $("act-tab-summaries-btn").setAttribute("aria-selected", String(!timeline));
    if (!timeline) loadActivitySummariesTab();
  }

  // The "Summaries" tab: saved summary runs, newest-first (server order). A profile selector
  // narrows the per-profile cards across every run; the history is cached so filtering is instant.
  async function loadActivitySummariesTab() {
    if (!ASUM) return;
    let r;
    try {
      r = await api("/review/activity/summaries");
    } catch (_e) {
      toast("Could not reach the guardian service.");
      return;
    }
    if (!r.ok) {
      toast("Could not load summaries (" + r.status + ").");
      return;
    }
    _actSummaries = ASUM.normalizeHistory((await r.json()).summaries || []);
    populateSummaryProfileFilter(_actSummaries);
    renderActivitySummaries();
  }

  function populateSummaryProfileFilter(runs) {
    const sel = $("act-sum-profile");
    const prev = sel.value;
    const names = [];
    const seen = new Set();
    for (const run of runs)
      for (const p of run.profiles)
        if (!seen.has(p.profile)) {
          seen.add(p.profile);
          names.push(p.profile);
        }
    sel.replaceChildren(
      el("option", { value: "", text: "All profiles" }),
      ...names.map((n) => el("option", { value: n, text: n })),
    );
    if (names.includes(prev)) sel.value = prev;
  }

  // Render the cached history, optionally narrowed to one profile. Runs with no matching profile
  // (after filtering) are dropped, so picking a kid shows only the runs that mention them.
  function renderActivitySummaries() {
    const filter = $("act-sum-profile").value;
    const shown = [];
    for (const run of _actSummaries) {
      const profiles = filter
        ? run.profiles.filter((p) => p.profile === filter)
        : run.profiles;
      if (profiles.length) shown.push(summaryRunCard(run, profiles));
    }
    $("act-summaries-empty").hidden = shown.length > 0;
    $("act-summaries-list").replaceChildren(...shown);
  }

  function summaryRunCard(run, profiles) {
    const when = run.generated_at ? timeAgo(run.generated_at) : "unknown time";
    const head = [
      el("span", { class: "summary-run__when", text: "Generated " + when }),
    ];
    if (typeof run.event_count === "number")
      head.push(
        el("span", {
          class: "muted summary-run__count",
          text:
            run.event_count + (run.event_count === 1 ? " event" : " events"),
        }),
      );
    return el(
      "div",
      { class: "summary-run" },
      el("div", { class: "summary-run__head" }, ...head),
      ...profiles.map(summaryProfileCard),
    );
  }

  function loadSection(key) {
    if (key === "dashboard") loadDashboard();
    else if (key === "profiles" && Aegis.loadProfiles) Aegis.loadProfiles();
    else if (key === "requests") loadRequests();
    else if (key === "activity") {
      setActivityTab("timeline"); // always land on the timeline; Summaries is opt-in per visit
      loadActivity();
    } else if (key === "whitelist") loadLists();
    else if (key === "time" && Aegis.loadTime) Aegis.loadTime();
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
    stopPolling();
    if (AS) AS.store.dispatch(AS.actions.requestsReset()); // clears badge + title via subscriber
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
    // Seed the requests store from this validation response (no extra fetch) and start polling.
    if (AS) {
      try {
        const d = await r.json();
        AS.store.dispatch(
          AS.actions.requestsLoaded(
            d.pending || [],
            d.recent || [],
            Date.now(),
          ),
        );
      } catch (_e) {
        /* seed best-effort; the first poll will populate the badge */
      }
      startPolling({ immediate: false });
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
    $("sk-add-btn").addEventListener("click", () => addListEntry(SK_ALLOW));
    $("sk-entry").addEventListener("keydown", (e) => {
      if (e.key === "Enter") addListEntry(SK_ALLOW);
    });
    $("skb-add-btn").addEventListener("click", () => addListEntry(SK_BLOCK));
    $("skb-entry").addEventListener("keydown", (e) => {
      if (e.key === "Enter") addListEntry(SK_BLOCK);
    });
    $("wl-profile").addEventListener("change", loadLists);
    $("cp-save").addEventListener("click", savePrompt);
    $("cp-reset").addEventListener("click", resetPrompt);
    $("act-profile").addEventListener("change", loadActivity);
    $("act-refresh").addEventListener("click", loadActivity);
    // The AI "Suggest rules" button only works with rules.js loaded; hide it otherwise.
    if (AR) $("act-suggest").addEventListener("click", suggestActivityRules);
    else $("act-suggest").hidden = true;
    $("act-tab-timeline-btn").addEventListener("click", () =>
      setActivityTab("timeline"),
    );
    $("act-tab-summaries-btn").addEventListener("click", () =>
      setActivityTab("summaries"),
    );
    $("act-sum-profile").addEventListener("change", renderActivitySummaries);
    if (ASUM)
      $("dash-summary-refresh").addEventListener("click", () =>
        refreshActivitySummary({ auto: false }),
      );
    else $("dash-summary").hidden = true;
    $("set-pin-btn").addEventListener("click", submitChangePin);
    $("set-pin-new").addEventListener("input", validatePinMatch);
    $("set-pin-confirm").addEventListener("input", validatePinMatch);
    initSidebar();
    window.addEventListener("hashchange", route);
    if (AS) {
      AS.store.subscribe(renderFromStore); // count badge + live lists react to store changes
      document.addEventListener("visibilitychange", onVisibility);
    }
    $("gate-pin").focus();
  });
})();
