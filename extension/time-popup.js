// Toolbar-button popup: shows the kid's remaining general screen-time, per-site credits for the
// current tab, and a "Request more time" action. An extension page, so it may read the bundled
// guardian config and call the backend directly (host permissions; no CORS), like block.js.

const $ = (id) => document.getElementById(id);

let CONFIG = null;
async function getConfig() {
  if (CONFIG) return CONFIG;
  try {
    CONFIG = await (
      await fetch(chrome.runtime.getURL("guardian-config.json"))
    ).json();
  } catch (_e) {
    CONFIG = { token: "", endpoint: "http://127.0.0.1:2947" };
  }
  return CONFIG;
}

function fmtMin(ms) {
  const m = Math.max(0, Math.round((ms || 0) / 60000));
  const h = Math.floor(m / 60);
  const mm = m % 60;
  if (h && mm) return h + "h " + mm + "m";
  if (h) return h + "h";
  return mm + "m";
}

function el(tag, props = {}, ...kids) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") n.className = v;
    else if (k === "text") n.textContent = v;
    else n.setAttribute(k, v === true ? "" : v);
  }
  for (const c of kids) if (c) n.append(c);
  return n;
}

async function activeUrl() {
  try {
    const [tab] = await chrome.tabs.query({
      active: true,
      currentWindow: true,
    });
    return (tab && tab.url) || "";
  } catch (_e) {
    return "";
  }
}

async function load() {
  const cfg = await getConfig();
  const url = await activeUrl();
  let state = null;
  try {
    const r = await fetch(
      `${cfg.endpoint}/time/state?url=${encodeURIComponent(url)}`,
      {
        headers: { "X-Guardian-Token": cfg.token },
      },
    );
    if (r.ok) state = await r.json();
  } catch (_e) {
    /* offline -> render the error state */
  }
  render(state, cfg);
}

function render(state, cfg) {
  const body = $("body");
  body.replaceChildren();
  if (!state) {
    body.append(
      el("p", { class: "muted", text: "Couldn't reach the guardian service." }),
    );
    return;
  }
  const g = state.general || {};
  if (g.limit_ms == null) {
    body.append(
      el("p", { class: "big", text: "No limit" }),
      el("p", {
        class: "muted",
        text: "No screen-time limit is set for this profile.",
      }),
    );
    return;
  }

  const rem = g.remaining_ms == null ? 0 : g.remaining_ms;
  body.append(el("p", { class: "big", text: fmtMin(rem) + " left" }));
  body.append(
    el("p", { class: "muted", text: "of " + fmtMin(g.limit_ms) + " today" }),
  );

  const pct =
    g.limit_ms > 0
      ? Math.min(100, Math.round(((g.used_ms || 0) / g.limit_ms) * 100))
      : 0;
  const barClass =
    "bar" + (rem <= 5 * 60000 ? " full" : rem <= 15 * 60000 ? " low" : "");
  const fill = el("i");
  fill.style.width = pct + "%";
  body.append(el("div", { class: barClass }, fill));

  if (state.bedtime && state.bedtime.active) {
    body.append(
      el(
        "div",
        { class: "site" },
        el("span", { class: "badge", text: "Bedtime" }),
        el("span", { text: "Browsing is paused right now." }),
      ),
    );
  }

  const s = state.site;
  if (s && s.host) {
    let line = "";
    if (s.excluded) line = "“" + s.host + "” doesn’t count against your time.";
    else if (s.limit_ms != null)
      line =
        "“" +
        s.host +
        "”: " +
        fmtMin(s.remaining_ms) +
        " left of " +
        fmtMin(s.limit_ms);
    if (line) body.append(el("div", { class: "site", text: line }));
  }

  const reason = el("textarea", {
    rows: "2",
    maxlength: "200",
    placeholder: "Why do you need more time?",
  });
  const btn = el("button", { type: "button", text: "Request more time" });
  const status = el("p", { id: "status" });
  btn.addEventListener("click", async () => {
    btn.disabled = true;
    status.textContent = "Sending…";
    try {
      const r = await fetch(`${cfg.endpoint}/time-request`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Guardian-Token": cfg.token,
        },
        body: JSON.stringify({ reason: reason.value.trim() }),
      });
      if (r.ok) {
        status.textContent =
          "Sent! Ask your parent to approve it, then reopen this.";
      } else {
        status.textContent = "Couldn’t send — try again later.";
        btn.disabled = false;
      }
    } catch (_e) {
      status.textContent = "Couldn’t reach the guardian service.";
      btn.disabled = false;
    }
  });
  body.append(el("label", { text: "Need more time?" }), reason, btn, status);
}

document.addEventListener("DOMContentLoaded", load);
