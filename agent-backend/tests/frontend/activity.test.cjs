/* Unit tests for the Activity-page event-consolidation pure logic (static/activity.js).
   Pure functions only — no DOM, no fetch (the browser glue in activity.js is guarded
   behind `typeof window`). Run: node --test agent-backend/tests/frontend/

   The feed emits one log line per classification step, so a single navigation can show as
   2-3 stacked rows (escalate -> "checking", then the terminal allow/block). consolidate()
   collapses a same-URL + same-profile burst (within a time window) into one row whose verdict
   is the most-recent terminal outcome. The audit log itself is never touched — display only. */

"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");

const {
  classifyEvent,
  consolidate,
} = require("../../src/agent_backend/guardian/static/activity.js");

// ISO timestamps a fixed number of seconds apart (newest-first feeds use larger = newer).
const BASE = Date.parse("2026-05-29T12:00:00.000Z");
function at(secondsAfterBase) {
  return new Date(BASE + secondsAfterBase * 1000).toISOString();
}
function ev(event, secondsAfterBase, extra) {
  return Object.assign(
    { event, ts: at(secondsAfterBase), url: "https://x.test/a", profile: "kid" },
    extra || {},
  );
}

// ---- classifyEvent --------------------------------------------------------

test("classifyEvent maps allow-family events to 'allowed'", () => {
  assert.equal(classifyEvent({ event: "allow" }), "allowed");
  assert.equal(classifyEvent({ event: "whitelist_allow" }), "allowed");
  assert.equal(classifyEvent({ event: "fail_open" }), "allowed");
});

test("classifyEvent maps block-family events to 'blocked'", () => {
  assert.equal(classifyEvent({ event: "block" }), "blocked");
  assert.equal(classifyEvent({ event: "blocklist_block" }), "blocked");
});

test("classifyEvent reads the verdict on a cache_hit", () => {
  assert.equal(classifyEvent({ event: "cache_hit", verdict: "block" }), "blocked");
  assert.equal(classifyEvent({ event: "cache_hit", verdict: "allow" }), "allowed");
});

test("classifyEvent maps escalate to the transient 'checking'", () => {
  assert.equal(classifyEvent({ event: "escalate" }), "checking");
});

test("classifyEvent labels anything outside the lifecycle 'other'", () => {
  assert.equal(classifyEvent({ event: "dwell" }), "other");
  assert.equal(classifyEvent({}), "other");
  assert.equal(classifyEvent(null), "other");
});

// ---- consolidate: guards --------------------------------------------------

test("consolidate returns [] for a non-array", () => {
  assert.deepEqual(consolidate(null), []);
  assert.deepEqual(consolidate(undefined), []);
  assert.deepEqual(consolidate("nope"), []);
});

test("consolidate passes a single event through with _count 1", () => {
  const out = consolidate([ev("block", 0)]);
  assert.equal(out.length, 1);
  assert.equal(classifyEvent(out[0]), "blocked");
  assert.equal(out[0]._count, 1);
});

// ---- consolidate: the reported case ---------------------------------------

test("consolidate collapses checking -> allowed -> blocked into one 'blocked' row", () => {
  // Newest-first feed: blocked (newest) , allowed , escalate/checking (oldest) — same URL.
  const feed = [ev("block", 40), ev("allow", 20), ev("escalate", 0)];
  const out = consolidate(feed);
  assert.equal(out.length, 1);
  assert.equal(classifyEvent(out[0]), "blocked"); // most-recent terminal wins
  assert.equal(out[0]._count, 3);
  assert.equal(out[0].url, "https://x.test/a");
  assert.equal(out[0].ts, at(40)); // row time = the most-recent event in the burst
});

test("consolidate shows the most-recent terminal even when checking is newest", () => {
  // Defensive ordering: a stray 'checking' newest, with a real terminal just behind it.
  const feed = [ev("escalate", 30), ev("block", 20), ev("allow", 10)];
  const out = consolidate(feed);
  assert.equal(out.length, 1);
  assert.equal(classifyEvent(out[0]), "blocked"); // newest terminal (block), not the checking
  assert.equal(out[0]._count, 3);
});

test("consolidate keeps 'checking' only when the burst never resolved", () => {
  const out = consolidate([ev("escalate", 20), ev("escalate", 0)]);
  assert.equal(out.length, 1);
  assert.equal(classifyEvent(out[0]), "checking");
  assert.equal(out[0]._count, 2);
});

test("consolidate drops the transient checking when a terminal exists", () => {
  const out = consolidate([ev("allow", 10), ev("escalate", 0)]);
  assert.equal(out.length, 1);
  assert.equal(classifyEvent(out[0]), "allowed");
  assert.equal(out[0]._count, 2);
});

// ---- consolidate: grouping boundaries -------------------------------------

test("consolidate does not merge different URLs", () => {
  const feed = [ev("block", 10, { url: "https://a.test/" }), ev("allow", 5, { url: "https://b.test/" })];
  const out = consolidate(feed);
  assert.equal(out.length, 2);
});

test("consolidate does not merge the same URL across different profiles", () => {
  const feed = [ev("block", 10, { profile: "alice" }), ev("allow", 5, { profile: "bob" })];
  const out = consolidate(feed);
  assert.equal(out.length, 2);
});

test("consolidate keys on url_key when present (falls back to url)", () => {
  const feed = [
    ev("block", 10, { url: "https://x.test/?a=1", url_key: "x.test/" }),
    ev("allow", 5, { url: "https://x.test/?a=2", url_key: "x.test/" }),
  ];
  const out = consolidate(feed);
  assert.equal(out.length, 1); // same canonical key -> one burst
  assert.equal(classifyEvent(out[0]), "blocked");
});

test("consolidate splits the same URL when events fall outside the window", () => {
  // 5 minutes apart, default 2-min window -> two separate rows.
  const feed = [ev("block", 300), ev("allow", 0)];
  const out = consolidate(feed);
  assert.equal(out.length, 2);
});

test("consolidate window boundary is inclusive", () => {
  const within = consolidate([ev("block", 120), ev("allow", 0)], { windowMs: 120000 });
  assert.equal(within.length, 1);
  const beyond = consolidate([ev("block", 121), ev("allow", 0)], { windowMs: 120000 });
  assert.equal(beyond.length, 2);
});

test("consolidate honours a custom windowMs", () => {
  const feed = [ev("block", 600), ev("allow", 0)];
  assert.equal(consolidate(feed, { windowMs: 15 * 60 * 1000 }).length, 1); // 15-min window
  assert.equal(consolidate(feed, { windowMs: 2 * 60 * 1000 }).length, 2); // 2-min window
});

test("consolidate does not merge across an interleaved different URL (A-B-A)", () => {
  const feed = [
    ev("allow", 30, { url: "https://a.test/" }),
    ev("block", 20, { url: "https://b.test/" }),
    ev("allow", 10, { url: "https://a.test/" }),
  ];
  const out = consolidate(feed);
  assert.equal(out.length, 3); // the two a.test visits are not adjacent -> stay separate
});

test("consolidate leaves non-lifecycle events as standalone rows", () => {
  const feed = [ev("block", 20), ev("dwell", 10), ev("allow", 0)];
  const out = consolidate(feed);
  assert.equal(out.length, 3); // 'dwell' is not mergeable and breaks the run
});

test("consolidate does not merge when a timestamp is missing/unparseable", () => {
  const feed = [ev("block", 10), ev("allow", 0, { ts: "not-a-date" })];
  const out = consolidate(feed);
  assert.equal(out.length, 2);
});

// ---- consolidate: immutability --------------------------------------------

test("consolidate never mutates its input", () => {
  const feed = [ev("block", 40), ev("allow", 20), ev("escalate", 0)];
  const snapshot = JSON.parse(JSON.stringify(feed));
  consolidate(feed);
  assert.deepEqual(feed, snapshot); // inputs untouched
  assert.equal(Object.prototype.hasOwnProperty.call(feed[0], "_count"), false);
});
