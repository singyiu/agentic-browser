/* Unit tests for the activity-summary pure shaping logic (static/summary.js).
   Pure functions only — no DOM, no fetch (the browser glue in summary.js is guarded
   behind `typeof window`). Run: node --test agent-backend/tests/frontend/ */

"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");

const {
  MAX_PROFILES,
  normalizeProfile,
  normalizeSummary,
  normalizeHistory,
  summaryIsEmpty,
} = require("../../src/agent_backend/guardian/static/summary.js");

// ---- normalizeProfile -----------------------------------------------------

test("normalizeProfile shapes a valid item", () => {
  const out = normalizeProfile({
    profile: "Hei",
    summary: "Mostly games and homework.",
    trends: ["more YouTube"],
    attention: ["tried a blocked site"],
  });
  assert.deepEqual(out, {
    profile: "Hei",
    summary: "Mostly games and homework.",
    trends: ["more YouTube"],
    attention: ["tried a blocked site"],
  });
});

test("normalizeProfile returns null without a usable profile name", () => {
  assert.equal(normalizeProfile({ summary: "no name" }), null);
  assert.equal(normalizeProfile({ profile: "   " }), null);
  assert.equal(normalizeProfile({ profile: { not: "a string" } }), null);
  assert.equal(normalizeProfile(null), null);
  assert.equal(normalizeProfile("string"), null);
});

test("normalizeProfile coerces, trims, drops containers, and caps list items", () => {
  const out = normalizeProfile({
    profile: "  A  ",
    summary: 42, // coerced to "42"
    trends: ["t1", 7, { bad: 1 }, ["nested"], "  t2  ", "", null],
    attention: ["a1", "a2", "a3", "a4", "a5", "a6", "a7", "a8"],
  });
  assert.equal(out.profile, "A");
  assert.equal(out.summary, "42");
  assert.deepEqual(out.trends, ["t1", "7", "t2"]); // number coerced; object/array/blank dropped
  assert.equal(out.attention.length, 6); // capped
});

test("normalizeProfile defaults missing trends/attention to []", () => {
  const out = normalizeProfile({ profile: "A", summary: "s" });
  assert.deepEqual(out.trends, []);
  assert.deepEqual(out.attention, []);
});

// ---- normalizeSummary -----------------------------------------------------

test("normalizeSummary shapes a full response", () => {
  const out = normalizeSummary({
    generated_at: "2026-05-30T12:00:00+00:00",
    stale: false,
    has_activity: true,
    profiles: [{ profile: "Hei", summary: "s", trends: [], attention: [] }],
  });
  assert.equal(out.generated_at, "2026-05-30T12:00:00+00:00");
  assert.equal(out.stale, false);
  assert.equal(out.has_activity, true);
  assert.equal(out.profiles[0].profile, "Hei");
});

test("normalizeSummary applies safe defaults for missing/garbage input", () => {
  for (const bad of [null, undefined, 7, "x", {}]) {
    const out = normalizeSummary(bad);
    assert.equal(out.generated_at, null);
    assert.equal(out.stale, false);
    assert.equal(out.has_activity, false);
    assert.deepEqual(out.profiles, []);
  }
});

test("normalizeSummary coerces stale/has_activity to strict booleans", () => {
  const out = normalizeSummary({ stale: "yes", has_activity: 1 });
  assert.equal(out.stale, false); // only === true counts
  assert.equal(out.has_activity, false);
});

test("normalizeSummary falls back to ts when generated_at is absent", () => {
  const out = normalizeSummary({ ts: "2026-05-30T01:00:00+00:00" });
  assert.equal(out.generated_at, "2026-05-30T01:00:00+00:00");
});

test("normalizeSummary drops non-array profiles and caps the count", () => {
  assert.deepEqual(normalizeSummary({ profiles: "nope" }).profiles, []);
  const many = Array.from({ length: 20 }, (_, i) => ({ profile: "P" + i, summary: "s" }));
  assert.equal(normalizeSummary({ profiles: many }).profiles.length, MAX_PROFILES);
});

test("normalizeSummary does not mutate its input", () => {
  const input = {
    stale: true,
    profiles: [{ profile: "A", summary: "s", trends: ["t"], attention: [] }],
  };
  const snapshot = JSON.parse(JSON.stringify(input));
  normalizeSummary(input);
  assert.deepEqual(input, snapshot);
});

// ---- normalizeHistory -----------------------------------------------------

test("normalizeHistory preserves order, shapes runs, coerces event_count", () => {
  const out = normalizeHistory([
    { ts: "t2", event_count: 12, profiles: [{ profile: "A", summary: "s" }] },
    { ts: "t1", event_count: "bad", profiles: [] },
  ]);
  assert.equal(out.length, 2);
  assert.equal(out[0].generated_at, "t2"); // newest-first order preserved
  assert.equal(out[0].event_count, 12);
  assert.equal(out[0].profiles[0].profile, "A");
  assert.equal(out[1].event_count, null); // non-numeric -> null
});

test("normalizeHistory returns [] for non-arrays and drops malformed runs", () => {
  assert.deepEqual(normalizeHistory(null), []);
  assert.deepEqual(normalizeHistory("x"), []);
  assert.deepEqual(normalizeHistory([null, 7, "s"]), []);
});

// ---- summaryIsEmpty -------------------------------------------------------

test("summaryIsEmpty is true with no profiles", () => {
  assert.equal(summaryIsEmpty({ profiles: [] }), true);
  assert.equal(summaryIsEmpty({}), true);
  assert.equal(summaryIsEmpty(null), true);
});

test("summaryIsEmpty is true when every profile is blank", () => {
  assert.equal(
    summaryIsEmpty({ profiles: [{ profile: "A", summary: "", trends: [], attention: [] }] }),
    true,
  );
});

test("summaryIsEmpty is false when any profile has content", () => {
  assert.equal(
    summaryIsEmpty({ profiles: [{ profile: "A", summary: "something", trends: [], attention: [] }] }),
    false,
  );
  assert.equal(
    summaryIsEmpty({ profiles: [{ profile: "A", summary: "", trends: ["t"], attention: [] }] }),
    false,
  );
});
