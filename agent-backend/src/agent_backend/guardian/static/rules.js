/* Pure logic for the Activity-page rule builder — deriving a block-rule entry from an
   activity item, validating it, fanning it out across selected profiles, and normalizing
   the AI's suggestion list. No DOM and no fetch live here so the branchable parts are unit
   tested under Node; shell.js keeps only the DOM glue.

   Loaded as a plain <script> before shell.js (exposes window.AegisRules); the same file is
   require()-able by Node tests (browser glue guarded behind `typeof window`, exports behind
   `typeof module`), exactly like store.js.

   Entry validation deliberately mirrors the backend's _review_list guard (non-empty, <=512
   chars, single line, printable) so the UI fails fast with a clear message instead of round-
   tripping to a 422. The backend remains the real authority. */

(function () {
  "use strict";

  // The four kinds the builder offers. exact/wildcard hard-match a URL; nl is a natural-language
  // topic the classifier treats as disallowed; ai just means the value was AI-drafted (it lands
  // as one of the others once written). Backend classify_entry auto-types the string regardless.
  const KINDS = [
    { id: "exact", label: "Exact URL" },
    { id: "wildcard", label: "Wildcard" },
    { id: "nl", label: "Natural language" },
    { id: "ai", label: "AI-suggested" },
  ];

  const _SCHEME = /^[a-z][a-z0-9+.-]*:\/\//;
  const _DOMAIN = /^[a-z0-9-]+(\.[a-z0-9-]+)+$/;
  // ASCII control chars (incl. DEL) — rejected so a single-line entry stays printable.
  // (Whitespace control chars are collapsed to a space before this check runs.)
  // eslint-disable-next-line no-control-regex
  const _CONTROL = /[\x00-\x1f\x7f]/;

  // Canonical host of a URL: lowercase, drop scheme + a leading "www.", keep only the host
  // (cut at the first / ? #). "" when the result isn't a dotted domain. Regex-only (no `new
  // URL`) so malformed input never throws — it just yields "".
  function hostOf(url) {
    const s = String(url == null ? "" : url)
      .trim()
      .toLowerCase();
    if (!s) return "";
    const host = s
      .replace(_SCHEME, "")
      .replace(/^www\./, "")
      .split(/[/?#]/)[0];
    return _DOMAIN.test(host) ? host : "";
  }

  // The value to pre-fill the box with for a given kind, derived from the activity item.
  // nl/ai start empty (the parent types, or the AI drafts via the suggest endpoint).
  function seedValue(ev, kind) {
    const url = (ev && (ev.url || ev.url_key)) || "";
    if (kind === "exact") return hostOf(url);
    if (kind === "wildcard") {
      const h = hostOf(url);
      return h ? h + "/*" : "";
    }
    return "";
  }

  // Trim + collapse whitespace to a single line, then validate. Returns {ok, value, error};
  // `value` is the cleaned string (also returned on error so the box can show what was checked).
  function normalizeEntry(value) {
    const collapsed = String(value == null ? "" : value)
      .replace(/\s+/g, " ")
      .trim();
    if (!collapsed) return { ok: false, value: "", error: "Enter a rule first." };
    if (collapsed.length > 512) {
      return { ok: false, value: collapsed, error: "Rule is too long (max 512 characters)." };
    }
    if (_CONTROL.test(collapsed)) {
      return { ok: false, value: collapsed, error: "Rule contains invalid characters." };
    }
    return { ok: true, value: collapsed, error: "" };
  }

  // Fan one entry out to the selected profiles -> [{entry, profile}] (one POST each to
  // /review/blocklist). "global" is a valid profile name. [] when the entry is invalid or no
  // profile is selected, so the caller shows a hint and skips the network entirely.
  function buildApplyPayloads(entry, profileNames) {
    const norm = normalizeEntry(entry);
    if (!norm.ok || !Array.isArray(profileNames)) return [];
    const seen = new Set();
    const out = [];
    for (const name of profileNames) {
      const profile = String(name == null ? "" : name).trim();
      if (!profile || seen.has(profile)) continue;
      seen.add(profile);
      out.push({ entry: norm.value, profile });
    }
    return out;
  }

  const _KINDS = new Set(["exact", "wildcard", "nl", "content", "ai"]);

  // Validate/clean the server's suggestions array into [{kind, value, reason}], dropping
  // anything malformed and clamping the count. Defensive even though the backend already
  // parses fail-safe — the list is rendered straight into the builder.
  function normalizeSuggestions(arr, max) {
    const limit = typeof max === "number" && max > 0 ? max : 8;
    if (!Array.isArray(arr)) return [];
    const out = [];
    for (const item of arr) {
      if (!item || typeof item !== "object") continue;
      const norm = normalizeEntry(item.value);
      if (!norm.ok) continue;
      let kind = String(item.kind == null ? "" : item.kind)
        .trim()
        .toLowerCase();
      if (!_KINDS.has(kind)) kind = "content";
      const reason = String(item.reason == null ? "" : item.reason)
        .trim()
        .slice(0, 300);
      out.push({ kind, value: norm.value, reason });
      if (out.length >= limit) break;
    }
    return out;
  }

  const api = {
    KINDS,
    hostOf,
    seedValue,
    normalizeEntry,
    buildApplyPayloads,
    normalizeSuggestions,
  };

  if (typeof window !== "undefined") {
    window.AegisRules = api;
  }
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  }
})();
