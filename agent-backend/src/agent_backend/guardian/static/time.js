/* Aegis Screen-time section — per-profile daily budget, bedtime windows, and per-site rules,
   editable directly or described in natural language (parsed by the AI into the structured
   form). Loaded after shell.js + time-core.js; reuses window.Aegis ($/el/api/toast) for the
   DOM helpers + PIN-aware fetch and window.AegisTime for pure shaping/formatting, and
   registers Aegis.loadTime for the hash router. All kid/parent strings render via el({text})
   (XSS-safe), matching shell.js. */

(() => {
  "use strict";

  const { $, el, api, toast } = window.Aegis;
  const T = window.AegisTime;

  // Working copy of the form, plus references to the live inputs so we can read it back.
  let _profiles = [];
  let _refs = null; // { defaultMin, dayMins:{}, windows:[...], sites:[...] }

  async function loadTime() {
    let r;
    try {
      r = await api("/profiles");
    } catch (_e) {
      toast("Could not reach the guardian service.");
      return;
    }
    if (!r.ok) return;
    _profiles = (await r.json()).profiles || [];
    const sel = $("time-profile");
    const prev = sel.value;
    sel.replaceChildren(
      ..._profiles.map((p) =>
        el("option", {
          value: p.name,
          text: p.is_global ? "Global — all kids" : p.name,
        }),
      ),
    );
    if (_profiles.some((p) => p.name === prev)) sel.value = prev;
    await loadPolicy();
    await loadUsage();
  }

  function selectedProfile() {
    return $("time-profile").value;
  }

  function isGlobalSelected() {
    return selectedProfile().toLowerCase() === "global";
  }

  async function loadPolicy() {
    const profile = selectedProfile();
    if (!profile) return;
    let r;
    try {
      r = await api("/time/policy?profile=" + encodeURIComponent(profile));
    } catch (_e) {
      toast("Could not reach the guardian service.");
      return;
    }
    if (!r.ok) return;
    const body = await r.json();
    const own = T.normalizePolicy(body.policy);
    const effective = T.normalizePolicy(body.effective);
    $("time-nl").value = own.source_text;
    renderForm(own);
    renderInheritHint(own, effective, body.is_global === true);
  }

  function renderInheritHint(own, effective, isGlobal) {
    const hint = $("time-active");
    if (isGlobal) {
      hint.textContent =
        "Global limits apply to every kid, unless a kid has their own.";
      return;
    }
    if (T.policyIsEmpty(own)) {
      const base = effective.daily_minutes.default;
      hint.textContent = T.policyIsEmpty(effective)
        ? "No limits yet for this kid (and no Global default). Browsing is unlimited."
        : "Inheriting Global limits" +
          (base != null ? " (" + T.formatMinutes(base) + "/day)" : "") +
          ". Saving here creates a per-kid override.";
    } else {
      hint.textContent =
        "This kid has their own limits (they win over Global).";
    }
  }

  /* ---------- form rendering ---------- */

  function renderForm(policy) {
    _refs = { defaultMin: null, dayMins: {}, windows: [], sites: [] };
    $("time-form").replaceChildren(
      generalCard(policy),
      bedtimeCard(policy),
      sitesCard(policy),
    );
  }

  function minutesInput(value, ariaLabel) {
    return el("input", {
      type: "number",
      min: "0",
      max: "1440",
      class: "time-min",
      inputmode: "numeric",
      "aria-label": ariaLabel,
      value: value == null ? "" : String(value),
    });
  }

  function field(labelText, control) {
    return el(
      "label",
      { class: "time-field" },
      el("span", { class: "settings-field", text: labelText }),
      control,
    );
  }

  function generalCard(policy) {
    const dm = policy.daily_minutes || {};
    _refs.defaultMin = minutesInput(dm.default, "Default daily minutes");
    const grid = el("div", { class: "time-day-grid" });
    grid.append(field("Every day (minutes)", _refs.defaultMin));
    for (const day of T.WEEKDAYS) {
      const input = minutesInput(
        dm[day],
        T.DAY_LABELS[day] + " override minutes",
      );
      _refs.dayMins[day] = input;
      grid.append(field(T.DAY_LABELS[day], input));
    }
    return el(
      "section",
      { class: "card time-block" },
      el("h2", { text: "Daily budget" }),
      el("p", {
        class: "muted",
        text: "Minutes of active browsing per day. Leave a day blank to use the everyday amount; leave everyday blank for no general limit.",
      }),
      grid,
    );
  }

  function bedtimeCard(policy) {
    const list = el("div", { class: "time-window-list" });
    const rerender = () => {
      const draft = readDraft();
      renderForm(draft);
    };
    (policy.windows || []).forEach((w, i) =>
      list.append(windowRow(w, i, rerender)),
    );
    const add = el("button", {
      class: "ghost",
      type: "button",
      text: "+ Add bedtime window",
    });
    add.addEventListener("click", () => {
      const draft = readDraft();
      draft.windows.push({ days: [], start: "21:00", end: "07:00" });
      renderForm(draft);
    });
    return el(
      "section",
      { class: "card time-block" },
      el("h2", { text: "Bedtime / blocked hours" }),
      el("p", {
        class: "muted",
        text: "Browsing is blocked during these hours regardless of remaining minutes. An end time earlier than the start wraps past midnight.",
      }),
      list,
      add,
    );
  }

  function windowRow(w, index, rerender) {
    const ref = { daysChecks: {}, start: null, end: null };
    const days = el("div", { class: "time-window-days" });
    for (const day of T.WEEKDAYS) {
      const cb = el("input", { type: "checkbox" });
      if ((w.days || []).includes(day)) cb.checked = true;
      ref.daysChecks[day] = cb;
      days.append(
        el(
          "label",
          { class: "time-day-check" },
          cb,
          el("span", { text: T.DAY_LABELS[day] }),
        ),
      );
    }
    ref.start = el("input", {
      type: "time",
      value: w.start || "",
      "aria-label": "Start time",
    });
    ref.end = el("input", {
      type: "time",
      value: w.end || "",
      "aria-label": "End time",
    });
    const remove = el("button", {
      class: "ghost time-remove",
      type: "button",
      text: "Remove",
    });
    remove.addEventListener("click", () => {
      const draft = readDraft();
      draft.windows.splice(index, 1);
      rerender();
    });
    _refs.windows.push(ref);
    return el(
      "div",
      { class: "time-window-row" },
      el(
        "div",
        { class: "time-window-times" },
        field("From", ref.start),
        field("To", ref.end),
        remove,
      ),
      el(
        "div",
        { class: "time-window-dayswrap" },
        el("span", { class: "settings-field", text: "Days (all = every day)" }),
        days,
      ),
    );
  }

  function sitesCard(policy) {
    const list = el("div", { class: "time-site-list" });
    const rerender = () => renderForm(readDraft());
    (policy.sites || []).forEach((s, i) =>
      list.append(siteRow(s, i, rerender)),
    );
    const add = el("button", {
      class: "ghost",
      type: "button",
      text: "+ Add site rule",
    });
    add.addEventListener("click", () => {
      const draft = readDraft();
      draft.sites.push({ host: "", daily_minutes: null, excluded: false });
      renderForm(draft);
    });
    return el(
      "section",
      { class: "card time-block" },
      el("h2", { text: "Per-site rules" }),
      el("p", {
        class: "muted",
        text: "Give a site its own cap, or exclude it from the general budget (it stays usable after the budget runs out — good for homework sites).",
      }),
      list,
      add,
    );
  }

  function siteRow(s, index, rerender) {
    const host = el("input", {
      type: "text",
      class: "time-site-host",
      placeholder: "khanacademy.org",
      value: s.host || "",
      "aria-label": "Site host",
    });
    const minutes = minutesInput(
      s.daily_minutes,
      "Site daily minutes (blank = no cap)",
    );
    const excluded = el("input", { type: "checkbox" });
    if (s.excluded) excluded.checked = true;
    const remove = el("button", {
      class: "ghost time-remove",
      type: "button",
      text: "Remove",
    });
    remove.addEventListener("click", () => {
      const draft = readDraft();
      draft.sites.splice(index, 1);
      rerender();
    });
    _refs.sites.push({ host, minutes, excluded });
    return el(
      "div",
      { class: "time-site-row" },
      field("Site", host),
      field("Own cap (min)", minutes),
      el(
        "label",
        { class: "time-site-exclude" },
        excluded,
        el("span", { text: "Exclude from general budget" }),
      ),
      remove,
    );
  }

  /* ---------- read the form back into a policy ---------- */

  function _minOrNull(input) {
    const v = (input.value || "").trim();
    if (v === "") return null;
    const n = Math.round(Number(v));
    if (!Number.isFinite(n)) return null;
    return Math.min(T.MAX_MINUTES, Math.max(0, n));
  }

  function readDraft() {
    if (!_refs) return T.normalizePolicy({});
    const daily = {};
    const def = _minOrNull(_refs.defaultMin);
    if (def != null) daily.default = def;
    for (const day of T.WEEKDAYS) {
      const n = _minOrNull(_refs.dayMins[day]);
      if (n != null) daily[day] = n;
    }
    const windows = _refs.windows
      .map((w) => ({
        days: T.WEEKDAYS.filter((d) => w.daysChecks[d].checked),
        start: w.start.value,
        end: w.end.value,
      }))
      .filter((w) => w.start && w.end);
    const sites = _refs.sites
      .map((s) => ({
        host: s.host.value.trim().toLowerCase(),
        daily_minutes: _minOrNull(s.minutes),
        excluded: s.excluded.checked,
      }))
      .filter((s) => s.host);
    return {
      daily_minutes: daily,
      windows,
      sites,
      source_text: $("time-nl").value,
    };
  }

  /* ---------- actions ---------- */

  async function parseNl() {
    const text = $("time-nl").value.trim();
    const hint = $("time-parse-hint");
    if (!text) {
      setHint(hint, "Describe the limits first.", true);
      return;
    }
    const btn = $("time-parse");
    btn.disabled = true;
    const label = btn.textContent;
    btn.textContent = "Parsing…";
    setHint(hint, "");
    try {
      const r = await api("/time/policy/parse", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ profile: selectedProfile(), text }),
      });
      if (r.ok) {
        const body = await r.json();
        const parsed = T.normalizePolicy(body.policy);
        parsed.source_text = text;
        renderForm(parsed);
        setHint(hint, "Filled the form below — review and Save.");
      } else if (r.status === 502) {
        setHint(
          hint,
          "The AI couldn't be reached. Edit the form below by hand.",
          true,
        );
      } else {
        setHint(hint, "Couldn't parse that (" + r.status + ").", true);
      }
    } catch (_e) {
      setHint(hint, "Could not reach the guardian service.", true);
    } finally {
      btn.disabled = false;
      btn.textContent = label;
    }
  }

  async function savePolicy() {
    const hint = $("time-save-hint");
    const btn = $("time-save");
    btn.disabled = true;
    const label = btn.textContent;
    btn.textContent = "Saving…";
    try {
      const r = await api(
        "/time/policy?profile=" + encodeURIComponent(selectedProfile()),
        {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(readDraft()),
        },
      );
      if (r.ok) {
        toast("Screen-time limits saved.");
        await loadPolicy();
        await loadUsage();
      } else {
        setHint(hint, "Couldn't save (" + r.status + ").", true);
      }
    } catch (_e) {
      setHint(hint, "Could not reach the guardian service.", true);
    } finally {
      btn.disabled = false;
      btn.textContent = label;
    }
  }

  async function loadUsage() {
    const box = $("time-usage");
    if (isGlobalSelected()) {
      box.replaceChildren(
        el("p", {
          class: "muted",
          text: "Usage is tracked per kid — pick a kid to see today's totals.",
        }),
      );
      return;
    }
    let r;
    try {
      r = await api("/review/time/usage");
    } catch (_e) {
      return;
    }
    if (!r.ok) return;
    const profiles = (await r.json()).profiles || [];
    const mine = profiles.find((p) => p.profile === selectedProfile());
    if (!mine) {
      box.replaceChildren(
        el("p", { class: "muted", text: "No usage recorded yet today." }),
      );
      return;
    }
    const u = T.normalizeUsage(mine);
    const parts = [];
    if (u.hasLimit) {
      parts.push(
        el("p", {
          class: "time-usage-line",
          text:
            "Used " +
            T.formatMs(u.usedMs) +
            " of " +
            T.formatMs(u.limitMs) +
            " today · " +
            T.formatMs(u.remainingMs) +
            " left",
        }),
      );
      parts.push(usageBar(u));
    } else {
      parts.push(
        el("p", {
          class: "time-usage-line",
          text: "Used " + T.formatMs(u.usedMs) + " today · no daily limit set",
        }),
      );
    }
    if (mine.bedtime_active) {
      parts.push(
        el("span", { class: "badge time-bedtime", text: "Bedtime active" }),
      );
    }
    box.replaceChildren(...parts);
  }

  function usageBar(u) {
    const pct =
      u.limitMs > 0
        ? Math.min(100, Math.round((u.usedMs / u.limitMs) * 100))
        : 0;
    const fill = el("div", {
      class: "time-bar__fill" + (u.blocked ? " is-full" : ""),
    });
    fill.style.width = pct + "%";
    return el("div", { class: "time-bar", role: "presentation" }, fill);
  }

  function setHint(node, msg, bad) {
    node.className = msg ? (bad ? "hint bad" : "hint ok") : "hint";
    node.textContent = msg;
  }

  window.Aegis.loadTime = loadTime;

  document.addEventListener("DOMContentLoaded", () => {
    $("time-profile").addEventListener("change", async () => {
      await loadPolicy();
      await loadUsage();
    });
    $("time-parse").addEventListener("click", parseNl);
    $("time-save").addEventListener("click", savePolicy);
  });
})();
