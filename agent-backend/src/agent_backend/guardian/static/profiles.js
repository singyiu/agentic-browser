/* Aegis Profiles section — create / rename / delete teen profiles, regenerate tokens,
   and the one-time token reveal. Loaded after shell.js; reuses window.Aegis ($/el/api/
   toast) for the DOM helpers + PIN-aware fetch, and registers Aegis.loadProfiles for the
   hash router. Kid-controlled strings are rendered via textContent only (XSS-safe),
   matching shell.js. */

(() => {
  "use strict";

  const { $, el, api, toast } = window.Aegis;

  /* ---------- List ---------- */
  async function loadProfiles() {
    let r;
    try {
      r = await api("/profiles");
    } catch (_e) {
      toast("Could not reach the guardian service.");
      return;
    }
    if (!r.ok) return;
    const profiles = (await r.json()).profiles || [];
    $("prof-list").replaceChildren(...profiles.map(profileCard));
    $("prof-empty").hidden = profiles.length > 0;
  }

  function profileCard(p) {
    const card = el("div", { class: "card prof-card" });
    const name = p.is_global ? "Global — all kids" : p.name;
    const counts =
      p.whitelist_count +
      " allowed · " +
      p.blocklist_count +
      " blocked" +
      (p.is_global ? "" : " · " + p.pending_count + " pending");
    card.append(
      el(
        "div",
        { class: "prof-card__head" },
        el("span", { class: "prof-card__name", text: name }),
        el("span", { class: "muted", text: counts }),
      ),
    );

    // Global is a shared, tokenless profile: no browser, so no rename/token/delete controls.
    if (p.is_global) {
      card.append(
        el("p", {
          class: "muted",
          text: "Applies to every kid, checked after each kid's own rules. Edit its prompt and allow/block lists in the Guard section.",
        }),
      );
      return card;
    }

    const rename = el("button", {
      class: "ghost",
      type: "button",
      text: "Rename",
    });
    rename.addEventListener("click", () => renameFlow(card, p.name));
    const token = el("button", {
      class: "ghost",
      type: "button",
      text: "Regenerate token",
    });
    token.addEventListener("click", () => regenerateFlow(card, p.name));
    const remove = el("button", {
      class: "reject",
      type: "button",
      text: "Delete",
    });
    remove.addEventListener("click", () => deleteFlow(card, p.name));

    card.append(el("div", { class: "prof-actions" }, rename, token, remove));
    return card;
  }

  /* ---------- Inline action panels (no native dialogs; mirrors the reject flow) ---------- */
  function inlinePanel(card, ...children) {
    closeInline(card);
    card.append(el("div", { class: "prof-inline" }, ...children));
  }

  function closeInline(card) {
    card.querySelectorAll(".prof-inline").forEach((n) => n.remove());
  }

  function row(...children) {
    return el("div", { class: "prof-inline__row" }, ...children);
  }

  function renameFlow(card, name) {
    const input = el("input", {
      class: "prof-inline__input",
      value: name,
      maxlength: "64",
      "aria-label": "New name for " + name,
    });
    const hint = el("p", { class: "hint" });
    const save = el("button", {
      class: "primary",
      type: "button",
      text: "Save",
    });
    const cancel = el("button", {
      class: "ghost",
      type: "button",
      text: "Cancel",
    });
    save.addEventListener("click", async () => {
      const r = await send(
        "/profiles/" + encodeURIComponent(name) + "/rename",
        { method: "POST", body: { new_name: input.value.trim() } },
        hint,
      );
      if (r && r.ok) {
        toast("Renamed");
        loadProfiles();
      } else if (r && r.status === 409) setHint(hint, "That name is taken.");
      else if (r && r.status === 422)
        setHint(hint, "Use letters, digits, '-' or '_' (max 64).");
    });
    cancel.addEventListener("click", () => closeInline(card));
    inlinePanel(card, row(input, save, cancel), hint);
    input.focus();
  }

  function regenerateFlow(card, name) {
    const go = el("button", {
      class: "primary",
      type: "button",
      text: "Regenerate",
    });
    const cancel = el("button", {
      class: "ghost",
      type: "button",
      text: "Cancel",
    });
    go.addEventListener("click", async () => {
      const r = await send("/profiles/" + encodeURIComponent(name) + "/token", {
        method: "POST",
      });
      if (r && r.ok) {
        closeInline(card);
        const body = await r.json();
        showTokenReveal({ name, token: body.token, config: body.config });
      }
    });
    cancel.addEventListener("click", () => closeInline(card));
    inlinePanel(
      card,
      el("p", {
        class: "prof-inline__warn",
        text:
          "Regenerate “" +
          name +
          "”’s token? The current browser token will stop working.",
      }),
      row(go, cancel),
    );
  }

  function deleteFlow(card, name) {
    const purge = el("input", { type: "checkbox" });
    const label = el(
      "label",
      { class: "prof-inline__check" },
      purge,
      el("span", {
        text: " Also erase this kid’s saved allow-list and history",
      }),
    );
    const go = el("button", {
      class: "reject",
      type: "button",
      text: "Confirm delete",
    });
    const cancel = el("button", {
      class: "ghost",
      type: "button",
      text: "Cancel",
    });
    go.addEventListener("click", async () => {
      const suffix = purge.checked ? "?purge=true" : "";
      const r = await send("/profiles/" + encodeURIComponent(name) + suffix, {
        method: "DELETE",
      });
      if (r && r.ok) {
        toast("Deleted");
        loadProfiles();
      }
    });
    cancel.addEventListener("click", () => closeInline(card));
    inlinePanel(
      card,
      el("p", {
        class: "prof-inline__warn",
        text: "Delete “" + name + "”? Their browser token will stop working.",
      }),
      label,
      row(go, cancel),
    );
  }

  /* ---------- Create + one-time token reveal ---------- */
  async function addProfile() {
    const input = $("prof-name");
    const name = input.value.trim();
    const hint = $("prof-hint");
    const btn = $("prof-add-btn");
    setHint(hint, "");
    if (!name) {
      setHint(hint, "Enter a name for this child.");
      return;
    }
    // /enroll creates the profile (if new) and packs this kid's browser extension with their
    // token + the guardian's LAN address baked in. Packing takes a few seconds — show progress.
    let r;
    const label = btn ? btn.textContent : "";
    if (btn) {
      btn.disabled = true;
      btn.textContent = "Setting up…";
    }
    try {
      r = await api("/enroll", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name }),
      });
    } catch (_e) {
      toast("Could not reach the guardian service.");
      return;
    } finally {
      if (btn) {
        btn.disabled = false;
        btn.textContent = label;
      }
    }
    if (r.status === 201) {
      input.value = "";
      showEnrollReveal(await r.json());
      loadProfiles();
      return;
    }
    if (r.status === 422)
      setHint(hint, "Use letters, digits, '-' or '_' (max 64).");
    else setHint(hint, "Could not set up this child (" + r.status + ").");
  }

  function showEnrollReveal(body) {
    const panel = el(
      "div",
      { class: "card prof-reveal" },
      el("h2", { text: "Set up " + body.profile + "’s Mac" }),
      el("p", {
        class: "muted",
        text:
          "On " +
          body.profile +
          "’s Mac, open this link in a browser. It downloads a setup file — double-click it " +
          "(if macOS warns it’s from an unidentified developer, right-click → Open) and enter " +
          "that Mac’s password once when asked. The locked browser installs itself.",
      }),
      codeBlock("Open on " + body.profile + "’s Mac", body.setup_url),
    );
    const done = el("button", {
      class: "primary",
      type: "button",
      text: "Done",
    });
    done.addEventListener("click", () => {
      $("prof-reveal").replaceChildren();
      $("prof-reveal").hidden = true;
    });
    panel.append(done);
    $("prof-reveal").replaceChildren(panel);
    $("prof-reveal").hidden = false;
    $("prof-reveal").scrollIntoView({ block: "nearest" });
  }

  function showTokenReveal(body) {
    const launch =
      "GUARDIAN_TOKEN=" + body.token + " ./scripts/launch-chromium.sh";
    const panel = el(
      "div",
      { class: "card prof-reveal" },
      el("h2", { text: "Token for " + body.name }),
      el("p", {
        class: "muted",
        text: "Copy this now — it is shown only once. Paste the config into this kid’s browser.",
      }),
      codeBlock("Token", body.token),
      codeBlock("guardian-config.json", JSON.stringify(body.config, null, 2)),
      codeBlock("Launch command", launch),
    );
    const done = el("button", {
      class: "primary",
      type: "button",
      text: "I’ve saved it",
    });
    done.addEventListener("click", () => {
      $("prof-reveal").replaceChildren();
      $("prof-reveal").hidden = true;
    });
    panel.append(done);
    $("prof-reveal").replaceChildren(panel);
    $("prof-reveal").hidden = false;
    $("prof-reveal").scrollIntoView({ block: "nearest" });
  }

  function codeBlock(label, text) {
    const pre = el("pre", { class: "prof-code" });
    pre.textContent = text;
    const copy = el("button", {
      class: "ghost prof-copy",
      type: "button",
      text: "Copy",
    });
    copy.addEventListener("click", () => copyText(text, copy));
    return el(
      "div",
      { class: "prof-codeblock" },
      el(
        "div",
        { class: "prof-codeblock__head" },
        el("span", { class: "settings-field", text: label }),
        copy,
      ),
      pre,
    );
  }

  async function copyText(text, btn) {
    try {
      await navigator.clipboard.writeText(text);
      const prev = btn.textContent;
      btn.textContent = "Copied ✓";
      setTimeout(() => {
        btn.textContent = prev;
      }, 1500);
    } catch (_e) {
      toast("Copy failed — select the text and copy manually.");
    }
  }

  /* ---------- Small shared helpers ---------- */
  function setHint(node, msg) {
    node.className = msg ? "hint bad" : "hint";
    node.textContent = msg;
  }

  // POST/DELETE JSON helper that surfaces transport errors as a toast and returns the
  // Response (or null on a network error) so callers can branch on status.
  async function send(path, opts, hint) {
    const init = { method: opts.method };
    if (opts.body !== undefined) {
      init.headers = { "Content-Type": "application/json" };
      init.body = JSON.stringify(opts.body);
    }
    try {
      return await api(path, init);
    } catch (_e) {
      if (hint) setHint(hint, "Could not reach the guardian service.");
      else toast("Could not reach the guardian service.");
      return null;
    }
  }

  window.Aegis.loadProfiles = loadProfiles;

  document.addEventListener("DOMContentLoaded", () => {
    $("prof-add-btn").addEventListener("click", addProfile);
    $("prof-name").addEventListener("keydown", (e) => {
      if (e.key === "Enter") addProfile();
    });
  });
})();
