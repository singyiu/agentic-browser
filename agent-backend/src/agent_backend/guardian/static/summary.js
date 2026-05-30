/* Pure shaping for the AI activity-summary feature — normalizing the dashboard summary
   response and the saved-summaries history into a safe, predictable shape before render.
   No DOM and no fetch live here so the branchable parts are unit tested under Node;
   shell.js keeps only the DOM glue.

   Loaded as a plain <script> before shell.js (exposes window.AegisSummary); the same file
   is require()-able by Node tests (browser glue guarded behind `typeof window`, exports
   behind `typeof module`), exactly like rules.js / store.js.

   This is defense-in-depth: the backend already parses model output fail-safe and the DOM
   factory (el({text})) is XSS-safe, but normalizing here guarantees arrays are arrays and
   strings are strings, so rendering can never throw on a surprising payload. Every function
   returns new objects/arrays and never mutates its input. */

(function () {
  "use strict";

  const MAX_PROFILES = 12; // matches the backend cap
  const MAX_ITEMS = 6; // per-profile trends / attention cap

  // A clean display string for a primitive; "" for null/undefined/objects/arrays (so a
  // surprising container never renders as "[object Object]"). Numbers/booleans coerce.
  function _text(value, maxLen) {
    if (value == null || typeof value === "object") return "";
    return String(value)
      .trim()
      .slice(0, maxLen || 600);
  }

  // A non-empty ISO timestamp string, else null.
  function _ts(value) {
    return typeof value === "string" && value ? value : null;
  }

  // Clean a server-supplied trends/attention array into bounded short strings: non-arrays ->
  // [], blank/container entries dropped, each trimmed + truncated, list capped.
  function _cleanList(value, maxLen) {
    if (!Array.isArray(value)) return [];
    const out = [];
    for (const entry of value) {
      const text = _text(entry, maxLen);
      if (text) out.push(text);
      if (out.length >= MAX_ITEMS) break;
    }
    return out;
  }

  // Shape one per-profile summary defensively into {profile, summary, trends[], attention[]}.
  // Returns null for anything without a usable profile name so callers can drop it.
  function normalizeProfile(item) {
    if (!item || typeof item !== "object") return null;
    const profile = _text(item.profile, 80);
    if (!profile) return null;
    return {
      profile,
      summary: _text(item.summary, 600),
      trends: _cleanList(item.trends, 200),
      attention: _cleanList(item.attention, 240),
    };
  }

  function _profiles(value) {
    if (!Array.isArray(value)) return [];
    const out = [];
    for (const item of value) {
      const p = normalizeProfile(item);
      if (p) out.push(p);
      if (out.length >= MAX_PROFILES) break;
    }
    return out;
  }

  // The dashboard GET /review/activity/summary response. generated_at falls back to a record's
  // ts; stale / has_activity coerced to strict booleans; profiles defensively shaped.
  function normalizeSummary(obj) {
    const o = obj && typeof obj === "object" ? obj : {};
    return {
      generated_at: _ts(o.generated_at) || _ts(o.ts),
      stale: o.stale === true,
      has_activity: o.has_activity === true,
      profiles: _profiles(o.profiles),
    };
  }

  // The Activity "Summaries" tab: each saved run -> {generated_at, event_count, profiles}.
  // Order is preserved (the server returns newest-first); malformed runs are dropped.
  function normalizeHistory(arr) {
    if (!Array.isArray(arr)) return [];
    const out = [];
    for (const run of arr) {
      if (!run || typeof run !== "object") continue;
      const count = Number(run.event_count);
      out.push({
        generated_at: _ts(run.generated_at) || _ts(run.ts),
        event_count: Number.isFinite(count) && count >= 0 ? count : null,
        profiles: _profiles(run.profiles),
      });
    }
    return out;
  }

  // A normalized summary (or run) is "empty" when it has no profiles, or every profile carries
  // no summary text, no trends and no attention items.
  function summaryIsEmpty(norm) {
    const profiles = norm && Array.isArray(norm.profiles) ? norm.profiles : [];
    if (!profiles.length) return true;
    return profiles.every(
      (p) =>
        !p.summary &&
        !(p.trends && p.trends.length) &&
        !(p.attention && p.attention.length),
    );
  }

  const api = {
    MAX_PROFILES,
    MAX_ITEMS,
    normalizeProfile,
    normalizeSummary,
    normalizeHistory,
    summaryIsEmpty,
  };

  if (typeof window !== "undefined") {
    window.AegisSummary = api;
  }
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  }
})();
