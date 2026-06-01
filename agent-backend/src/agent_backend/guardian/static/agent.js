// Guardian "Agent" page — the flagship conversational assistant (default landing).
//
// A chat where the parent can ask about the software, analyze their kids' browsing, and ask for
// configuration changes. The model never writes: it returns a reply plus optional *proposals* the
// parent applies with one click (POST /agent/apply) and follow-up *suggestions*. Conversation
// state lives here and is re-sent (bounded) each turn; the backend is stateless.
//
// Reuses shell.js's shared helpers via window.Aegis: el() (DOM factory), api() (fetch with the
// parent-PIN header injected), toast() (notifications). Tokens/component classes only — no inline
// colors; styling lives in aegis-shell.css.
(function () {
  "use strict";

  const HISTORY_MAX = 20; // turns re-sent per request (matches the server-side cap)

  const msgs = []; // [{ role: "user"|"assistant", content }]
  let profile = null; // selected profile scope (null = all)
  let mounted = false;
  let thinking = false;

  // DOM refs (built once on first load)
  let versionBar, transcript, suggestRow, input, profileSelect, sendBtn, intro, thinkingEl;

  function A() {
    return window.Aegis || null;
  }

  function scrollToBottom() {
    if (transcript) transcript.scrollTop = transcript.scrollHeight;
  }

  function removeIntro() {
    if (intro && intro.parentNode) {
      intro.remove();
      intro = null;
    }
  }

  function addMessage(role, content) {
    msgs.push({ role: role, content: content });
    removeIntro();
    const bubble = A().el("div", { class: "agent-msg__bubble", text: content });
    transcript.append(A().el("div", { class: "agent-msg agent-msg--" + role }, bubble));
    scrollToBottom();
  }

  function setThinking(on) {
    thinking = on;
    sendBtn.disabled = on;
    input.disabled = on;
    if (on) {
      const dots = A().el(
        "div",
        { class: "agent-thinking", "aria-label": "Assistant is thinking" },
        A().el("span", { class: "agent-dot" }),
        A().el("span", { class: "agent-dot" }),
        A().el("span", { class: "agent-dot" })
      );
      thinkingEl = A().el("div", { class: "agent-msg agent-msg--assistant" }, dots);
      transcript.append(thinkingEl);
      scrollToBottom();
    } else if (thinkingEl) {
      thinkingEl.remove();
      thinkingEl = null;
    }
  }

  // A human-readable summary of a proposed change for the confirmation card.
  function proposalTitle(p) {
    const who = p.profile || "all profiles";
    const params = p.params || {};
    const e = params.entry || params.text || params.prompt || "";
    switch (p.action) {
      case "whitelist.add":
        return "Allow “" + e + "” for " + who;
      case "whitelist.remove":
        return "Stop allowing “" + e + "” for " + who;
      case "blocklist.add":
        return "Block “" + e + "” for " + who;
      case "blocklist.remove":
        return "Unblock “" + e + "” for " + who;
      case "search_allow.add":
        return "Allow search keyword “" + e + "” for " + who;
      case "search_allow.remove":
        return "Remove allowed search keyword “" + e + "” for " + who;
      case "search_block.add":
        return "Block search keyword “" + e + "” for " + who;
      case "search_block.remove":
        return "Unblock search keyword “" + e + "” for " + who;
      case "time_policy.set":
        return "Set screen-time for " + who + ": " + e;
      case "prize.grant":
        return "Grant " + params.points + " prize points to " + who;
      case "prompt.set":
        return "Update " + who + "'s classification prompt";
      default:
        return p.action;
    }
  }

  async function applyProposal(p, card, applyBtn) {
    applyBtn.disabled = true;
    applyBtn.textContent = "Applying…";
    try {
      const r = await A().api("/agent/apply", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: p.action, profile: p.profile, params: p.params || {} }),
      });
      const data = await r.json().catch(function () {
        return {};
      });
      if (r.ok) {
        A().toast("Applied ✓");
        card.classList.add("agent-proposal--done");
        card.replaceChildren(
          A().el("div", {
            class: "agent-proposal__title",
            text: "✓ " + proposalTitle(p),
          })
        );
      } else {
        A().toast((data && data.error) || "Could not apply that change.");
        applyBtn.disabled = false;
        applyBtn.textContent = "Apply";
      }
    } catch (_e) {
      A().toast("Network error applying the change.");
      applyBtn.disabled = false;
      applyBtn.textContent = "Apply";
    }
  }

  function renderProposal(p) {
    const applyBtn = A().el("button", { class: "primary", type: "button", text: "Apply" });
    const dismissBtn = A().el("button", { class: "ghost", type: "button", text: "Dismiss" });
    const card = A().el(
      "div",
      { class: "agent-proposal" },
      A().el("div", { class: "agent-proposal__title", text: proposalTitle(p) }),
      p.rationale ? A().el("div", { class: "agent-proposal__why", text: p.rationale }) : null,
      A().el("div", { class: "agent-proposal__actions" }, applyBtn, dismissBtn)
    );
    applyBtn.addEventListener("click", function () {
      applyProposal(p, card, applyBtn);
    });
    dismissBtn.addEventListener("click", function () {
      card.remove();
    });
    transcript.append(card);
    scrollToBottom();
  }

  function renderSuggestions(list) {
    suggestRow.replaceChildren();
    (list || []).forEach(function (s) {
      const chip = A().el("button", { class: "agent-chip", type: "button", text: s });
      chip.addEventListener("click", function () {
        send(s);
      });
      suggestRow.append(chip);
    });
  }

  async function send(text) {
    const body = (text == null ? input.value : text).trim();
    if (!body || thinking) return;
    if (text == null) input.value = "";
    resizeInput();
    addMessage("user", body);
    suggestRow.replaceChildren();
    setThinking(true);
    let data = null;
    let ok = false;
    try {
      const r = await A().api("/agent/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ messages: msgs.slice(-HISTORY_MAX), profile: profile }),
      });
      ok = r.ok;
      data = await r.json().catch(function () {
        return null;
      });
    } catch (_e) {
      ok = false;
    }
    setThinking(false);
    if (!ok || !data) {
      A().toast((data && data.error) || "The assistant is unavailable. Please try again.");
    } else {
      addMessage("assistant", data.reply || "(no reply)");
      (data.proposals || []).forEach(renderProposal);
      renderSuggestions(data.suggestions);
    }
    input.focus();
  }

  async function fetchVersion() {
    try {
      const r = await A().api("/version");
      if (!r.ok) return;
      const v = await r.json();
      const items = [];
      if (v.guardian) items.push("Guardian v" + v.guardian);
      if (v.extension) items.push("Extension v" + v.extension);
      if (v.grafana && v.grafana.lgtm) items.push("Grafana " + v.grafana.lgtm);
      if (v.model) items.push("Model " + v.model);
      versionBar.replaceChildren();
      items.forEach(function (t) {
        versionBar.append(A().el("span", { class: "agent__stack-item", text: t }));
      });
    } catch (_e) {
      /* the version bar is best-effort */
    }
  }

  async function fetchProfiles() {
    try {
      const r = await A().api("/profiles");
      if (!r.ok) return;
      const data = await r.json();
      (data.profiles || []).forEach(function (p) {
        if (p.is_global) return;
        profileSelect.append(A().el("option", { value: p.name, text: p.name }));
      });
    } catch (_e) {
      /* the scope selector is optional */
    }
  }

  function resizeInput() {
    if (!input) return;
    input.style.height = "auto";
    input.style.height = Math.min(input.scrollHeight, Math.round(window.innerHeight * 0.4)) + "px";
  }

  function buildUI(root) {
    const el = A().el;
    versionBar = el("div", { class: "agent__bar", id: "agent-version" });
    transcript = el("div", {
      class: "agent__transcript",
      id: "agent-transcript",
      "aria-live": "polite",
    });
    intro = el("p", {
      class: "agent__intro",
      text:
        "Ask me anything about this browser, analyze your kids’ activity, or ask me to " +
        "change a setting — I’ll propose changes for you to approve.",
    });
    transcript.append(intro);
    suggestRow = el("div", { class: "agent__suggest", id: "agent-suggest" });
    profileSelect = el(
      "select",
      { id: "agent-profile", "aria-label": "Limit to a profile" },
      el("option", { value: "", text: "All profiles" })
    );
    input = el("textarea", {
      class: "agent__input",
      id: "agent-input",
      rows: "1",
      placeholder: "Message the agent…",
      "aria-label": "Message the agent",
    });
    sendBtn = el("button", {
      class: "primary agent__send",
      id: "agent-send",
      type: "button",
      text: "Send",
    });
    const composer = el("div", { class: "agent__composer" }, profileSelect, input, sendBtn);
    root.append(el("div", { class: "agent" }, versionBar, transcript, suggestRow, composer));

    sendBtn.addEventListener("click", function () {
      send();
    });
    input.addEventListener("input", resizeInput);
    input.addEventListener("keydown", function (ev) {
      if (ev.key === "Enter" && !ev.shiftKey) {
        ev.preventDefault();
        send();
      }
    });
    profileSelect.addEventListener("change", function () {
      profile = profileSelect.value || null;
    });
  }

  function load() {
    if (!A()) return; // shell.js (window.Aegis) must be present; it always is at route time
    const root = A().$("agent-root");
    if (!root) return;
    if (!mounted) {
      mounted = true;
      buildUI(root);
      fetchVersion();
      fetchProfiles();
    }
    if (input) input.focus();
  }

  window.AegisAgent = { load: load };
})();
