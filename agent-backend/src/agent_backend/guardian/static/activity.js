/* Pure logic for consolidating the Activity-page timeline. One navigation emits several log
   lines (escalate -> "checking", then the terminal allow/block after the screenshot round-trip),
   so the same URL stacks as 2-3 rows seconds apart. consolidate() collapses a same-URL +
   same-profile burst into a single row using a safety-first verdict: blocked > allowed >
   checking (a block is never hidden by a later, often low-confidence, allow). The transient
   "checking" is shown only when the burst never produced a terminal verdict.

   Display only — the append-only audit log (event_log) is never modified. No DOM and no fetch
   live here so the branchable parts are unit tested under Node; shell.js keeps the DOM glue.

   Loaded as a plain <script> before shell.js (exposes window.AegisActivity); the same file is
   require()-able by Node tests (browser glue behind `typeof window`, exports behind
   `typeof module`), exactly like rules.js / store.js. */

(function () {
  "use strict";

  // Default burst window: events for the same URL within ~2 minutes are one navigation.
  const DEFAULT_WINDOW_MS = 2 * 60 * 1000;

  // The lifecycle states that may be merged together; anything else stands alone.
  const MERGEABLE = { allowed: true, blocked: true, checking: true };

  // Map a raw activity event to one display state. Mirrors activityVerdict() in shell.js,
  // kept here as pure logic so consolidation is testable. Unknown events -> "other" (never merged).
  function classifyEvent(ev) {
    const e = ev && ev.event;
    if (e === "cache_hit")
      return ev.verdict === "block" ? "blocked" : "allowed";
    if (e === "block" || e === "blocklist_block") return "blocked";
    if (e === "allow" || e === "whitelist_allow" || e === "fail_open")
      return "allowed";
    if (e === "escalate") return "checking";
    return "other";
  }

  // Canonical grouping key: same kid + same page. url_key is the classifier's canonical form;
  // fall back to the full url. Different profiles never merge even on an identical URL.
  function keyOf(ev) {
    const url = (ev && (ev.url_key || ev.url)) || "";
    const profile = (ev && ev.profile) || "";
    return profile + "\n" + url;
  }

  // Epoch ms for an event's ts, or NaN if absent/unparseable (NaN -> never merges, fails safe).
  function tsMs(ev) {
    const ms = Date.parse((ev && ev.ts) || "");
    return Number.isNaN(ms) ? NaN : ms;
  }

  // True when `ts` sits within `windowMs` before the group's newest ts. Feeds are newest-first,
  // so newestTs >= ts; a negative gap (out-of-order) or any NaN refuses the merge.
  function withinWindow(newestTs, ts, windowMs) {
    if (Number.isNaN(newestTs) || Number.isNaN(ts)) return false;
    const gap = newestTs - ts;
    return gap >= 0 && gap <= windowMs;
  }

  // Reduce one burst (newest-first) to a single row using safety-first precedence:
  // blocked > allowed > checking. A block is never hidden by a later (often low-confidence
  // fallback) allow; within a tier the most-recent event wins. Returns a NEW object (input
  // never mutated) carrying `_count` and the burst's newest ts (so "X ago" reflects the
  // latest activity).
  function pickWinner(groupEvents) {
    let blocked = null;
    let allowed = null;
    for (const ev of groupEvents) {
      const label = classifyEvent(ev); // newest-first: first hit in a tier is the most recent
      if (label === "blocked") {
        if (!blocked) blocked = ev;
      } else if (label === "allowed") {
        if (!allowed) allowed = ev;
      }
    }
    const winner = blocked || allowed || groupEvents[0];
    return Object.assign({}, winner, {
      _count: groupEvents.length,
      ts: groupEvents[0].ts,
    });
  }

  // Collapse a newest-first list of activity events. Consecutive events sharing (profile, url)
  // within `windowMs` of the group's newest event become one row. Non-lifecycle events
  // ("other") and anything with a missing/odd timestamp pass through as their own row.
  function consolidate(events, opts) {
    if (!Array.isArray(events)) return [];
    const windowMs =
      opts && typeof opts.windowMs === "number" && opts.windowMs >= 0
        ? opts.windowMs
        : DEFAULT_WINDOW_MS;

    const out = [];
    let group = null; // { key, newestTs, events: [] }

    const flush = () => {
      if (group) {
        out.push(pickWinner(group.events));
        group = null;
      }
    };

    for (const ev of events) {
      if (!ev || typeof ev !== "object" || !MERGEABLE[classifyEvent(ev)]) {
        flush();
        if (ev && typeof ev === "object")
          out.push(Object.assign({}, ev, { _count: 1 }));
        continue;
      }
      const key = keyOf(ev);
      const ts = tsMs(ev);
      if (
        group &&
        group.key === key &&
        withinWindow(group.newestTs, ts, windowMs)
      ) {
        group.events.push(ev);
      } else {
        flush();
        group = { key, newestTs: ts, events: [ev] };
      }
    }
    flush();
    return out;
  }

  const api = { classifyEvent, consolidate, DEFAULT_WINDOW_MS };

  if (typeof window !== "undefined") {
    window.AegisActivity = api;
  }
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  }
})();
