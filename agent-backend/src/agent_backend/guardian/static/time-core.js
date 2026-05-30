/* Pure shaping + formatting for the screen-time feature — normalizing the policy and usage
   responses into a safe, predictable shape, and formatting durations for display. No DOM and
   no fetch live here, so the branchable parts are unit-tested under Node; time.js keeps only
   the DOM glue.

   Loaded as a plain <script> before shell.js (exposes window.AegisTime); the same file is
   require()-able by Node tests (browser glue behind `typeof window`, exports behind
   `typeof module`), exactly like summary.js / rules.js.

   Defense-in-depth: the backend already validates/clamps, but normalizing here guarantees
   arrays are arrays and numbers are numbers so rendering can never throw. Every function
   returns new objects/arrays and never mutates its input. */

(function () {
  "use strict";

  const WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"];
  const DAY_KEYS = ["default"].concat(WEEKDAYS);
  const DAY_LABELS = {
    default: "Every day",
    mon: "Mon",
    tue: "Tue",
    wed: "Wed",
    thu: "Thu",
    fri: "Fri",
    sat: "Sat",
    sun: "Sun",
  };
  const MAX_MINUTES = 1440;
  const MAX_SITES = 50;
  const MAX_WINDOWS = 14;

  // A number coerced into [lo, hi] (rounded). Non-numeric / boolean -> null (drop the value).
  function _clampInt(value, lo, hi) {
    if (typeof value === "boolean") return null;
    const n = Number(value);
    if (!Number.isFinite(n)) return null;
    return Math.min(hi, Math.max(lo, Math.round(n)));
  }

  function _str(value) {
    if (value == null || typeof value === "object") return "";
    return String(value).trim();
  }

  function _dailyMinutes(raw) {
    const out = {};
    if (!raw || typeof raw !== "object") return out;
    for (const key of DAY_KEYS) {
      if (!(key in raw)) continue;
      const n = _clampInt(raw[key], 0, MAX_MINUTES);
      if (n !== null) out[key] = n;
    }
    return out;
  }

  function _days(raw) {
    if (!Array.isArray(raw)) return [];
    const out = [];
    for (const d of raw) {
      const key = _str(d).toLowerCase();
      if (WEEKDAYS.includes(key) && !out.includes(key)) out.push(key);
    }
    return out;
  }

  function _windows(raw) {
    if (!Array.isArray(raw)) return [];
    const out = [];
    for (const w of raw) {
      if (!w || typeof w !== "object") continue;
      const start = _str(w.start);
      const end = _str(w.end);
      if (!start || !end) continue;
      out.push({ days: _days(w.days), start, end });
      if (out.length >= MAX_WINDOWS) break;
    }
    return out;
  }

  function _sites(raw) {
    if (!Array.isArray(raw)) return [];
    const out = [];
    for (const s of raw) {
      if (!s || typeof s !== "object") continue;
      const host = _str(s.host).toLowerCase();
      if (!host) continue;
      // null/absent = no own cap; a real value is clamped into range.
      const minutes =
        s.daily_minutes == null
          ? null
          : _clampInt(s.daily_minutes, 0, MAX_MINUTES);
      out.push({ host, daily_minutes: minutes, excluded: s.excluded === true });
      if (out.length >= MAX_SITES) break;
    }
    return out;
  }

  // Shape a policy (own or effective) into {daily_minutes, windows, sites, source_text, updated_ts}.
  function normalizePolicy(obj) {
    const o = obj && typeof obj === "object" ? obj : {};
    return {
      daily_minutes: _dailyMinutes(o.daily_minutes),
      windows: _windows(o.windows),
      sites: _sites(o.sites),
      source_text: _str(o.source_text),
      updated_ts: _str(o.updated_ts),
    };
  }

  // True when a policy carries no actual configuration (drives the "inherits Global" hint).
  function policyIsEmpty(policy) {
    const p = normalizePolicy(policy);
    return (
      Object.keys(p.daily_minutes).length === 0 &&
      p.windows.length === 0 &&
      p.sites.length === 0
    );
  }

  // The general-pool view from a /time/state or /review/time/usage entry (accepts either the
  // whole object with a `.general` key or a bare general object).
  function normalizeUsage(obj) {
    const root = obj && typeof obj === "object" ? obj : {};
    const g =
      root.general && typeof root.general === "object" ? root.general : root;
    const MAX = Number.MAX_SAFE_INTEGER;
    const used = _clampInt(g.used_ms, 0, MAX) || 0;
    const limit = g.limit_ms == null ? null : _clampInt(g.limit_ms, 0, MAX); // null = unlimited
    let remaining = null;
    if (limit !== null) {
      const r = _clampInt(g.remaining_ms, 0, MAX);
      remaining = r === null ? 0 : r;
    }
    return {
      usedMs: used,
      limitMs: limit,
      remainingMs: remaining,
      blocked: g.blocked === true,
      hasLimit: limit !== null,
    };
  }

  // "0m" / "45m" / "1h" / "1h 30m" / "24h" from a minute count.
  function formatMinutes(min) {
    const total = Math.max(0, Math.round(Number(min) || 0));
    const h = Math.floor(total / 60);
    const m = total % 60;
    if (h && m) return h + "h " + m + "m";
    if (h) return h + "h";
    return m + "m";
  }

  function formatMs(ms) {
    return formatMinutes((Number(ms) || 0) / 60000);
  }

  const api = {
    WEEKDAYS,
    DAY_KEYS,
    DAY_LABELS,
    MAX_MINUTES,
    MAX_SITES,
    MAX_WINDOWS,
    normalizePolicy,
    policyIsEmpty,
    normalizeUsage,
    formatMinutes,
    formatMs,
  };

  if (typeof window !== "undefined") {
    window.AegisTime = api;
  }
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  }
})();
