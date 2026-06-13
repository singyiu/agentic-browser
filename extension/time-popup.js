// Toolbar-button popup: shows the kid's remaining general screen-time, per-site credits for the
// current tab, and a "Request more time" action. An extension page, so it may read the bundled
// guardian config and call the backend directly (host permissions; no CORS), like block.js.
// Runs as an ES module so the guardian config loader is shared with the service worker.

import { getConfig } from "./guardian-client.js";

const $ = (id) => document.getElementById(id);

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
  let prize = null;
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
  try {
    const r = await fetch(`${cfg.endpoint}/prize-points`, {
      headers: { "X-Guardian-Token": cfg.token },
    });
    if (r.ok) prize = await r.json();
  } catch (_e) {
    /* no points service -> just omit the prize section */
  }
  render(state, cfg, prize);
}

// A balance line + package buttons for spending points on more time (#3: check balance from the
// icon; #2: redeem from the icon). Returns null when there's nothing useful to show.
function prizeBlock(prize, cfg) {
  if (!prize) return null;
  const balance = Number(prize.balance || 0);
  const packages = Array.isArray(prize.packages) ? prize.packages : [];
  const wrap = el(
    "div",
    { class: "pp" },
    el("p", {
      class: "pp__bal",
      text:
        "🎟️ " + balance + (balance === 1 ? " prize point" : " prize points"),
    }),
  );
  const affordable = packages.filter((p) => p.affordable);
  if (balance > 0 && affordable.length) {
    const status = el("p", { class: "pp__status" });
    const pkgs = el("div", { class: "pp__pkgs" });
    for (const p of packages) {
      const b = el("button", {
        type: "button",
        text: "+" + p.minutes + "m · " + p.cost,
        title: "Spend " + p.cost + " points for " + p.minutes + " more minutes",
      });
      b.disabled = !p.affordable;
      b.addEventListener("click", () =>
        redeemPoints(p.minutes, cfg, pkgs, status),
      );
      pkgs.append(b);
    }
    wrap.append(
      el("p", { class: "pp__lead", text: "Spend points for more time:" }),
      pkgs,
      status,
    );
  }
  return wrap;
}

async function redeemPoints(minutes, cfg, pkgs, status) {
  const btns = [...pkgs.querySelectorAll("button")];
  const wasEnabled = btns.filter((b) => !b.disabled);
  btns.forEach((b) => (b.disabled = true));
  status.textContent = "Redeeming…";
  try {
    const r = await fetch(`${cfg.endpoint}/prize-points/redeem`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Guardian-Token": cfg.token,
      },
      body: JSON.stringify({ minutes }),
    });
    const data = await r.json().catch(() => ({}));
    if (r.ok && data.ok) {
      status.textContent = "Added " + minutes + " min!";
      load(); // refresh the time bar + balance
    } else {
      status.textContent =
        data.error === "daily_cap_reached"
          ? "Daily bonus limit reached."
          : data.error === "insufficient_points"
            ? "Not enough points."
            : "Couldn’t redeem.";
      wasEnabled.forEach((b) => (b.disabled = false));
    }
  } catch (_e) {
    status.textContent = "Couldn’t reach the guardian.";
    wasEnabled.forEach((b) => (b.disabled = false));
  }
}

function render(state, cfg, prize) {
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
    const ppNoLimit = prizeBlock(prize, cfg);
    if (ppNoLimit) body.append(ppNoLimit);
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

  const pp = prizeBlock(prize, cfg);
  if (pp) body.append(pp);

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
