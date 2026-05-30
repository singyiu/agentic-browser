/* Unit tests for the screen-time pure shaping/formatting logic (static/time-core.js).
   Pure functions only — no DOM, no fetch. Run: node --test agent-backend/tests/frontend/ */

"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");

const {
  normalizePolicy,
  policyIsEmpty,
  normalizeUsage,
  normalizeTimeRequests,
  formatMinutes,
  formatMs,
} = require("../../src/agent_backend/guardian/static/time-core.js");

// ---- normalizePolicy ------------------------------------------------------

test("normalizePolicy shapes a full policy", () => {
  const p = normalizePolicy({
    daily_minutes: { default: 120, sat: 240, bogus: 10 },
    windows: [{ days: ["mon", "tue", "xx"], start: "21:00", end: "07:00" }],
    sites: [{ host: "KhanAcademy.org", daily_minutes: null, excluded: true }],
    source_text: "  two hours  ",
    updated_ts: "2026-05-30T00:00:00+00:00",
  });
  assert.deepEqual(p.daily_minutes, { default: 120, sat: 240 }); // unknown key dropped
  assert.deepEqual(p.windows, [{ days: ["mon", "tue"], start: "21:00", end: "07:00" }]);
  assert.deepEqual(p.sites, [{ host: "khanacademy.org", daily_minutes: null, excluded: true }]);
  assert.equal(p.source_text, "two hours");
});

test("normalizePolicy clamps minutes and drops invalid values", () => {
  const p = normalizePolicy({ daily_minutes: { default: 99999, mon: -5, tue: true, wed: "x" } });
  assert.deepEqual(p.daily_minutes, { default: 1440, mon: 0 }); // true & "x" dropped, -5 clamped
});

test("normalizePolicy drops windows missing a time and sites missing a host", () => {
  const p = normalizePolicy({
    windows: [{ start: "21:00" }, { start: "22:00", end: "06:00" }],
    sites: [{ excluded: true }, { host: "g.com", daily_minutes: 30 }],
  });
  assert.equal(p.windows.length, 1);
  assert.deepEqual(p.sites, [{ host: "g.com", daily_minutes: 30, excluded: false }]);
});

test("normalizePolicy is total on garbage", () => {
  assert.deepEqual(normalizePolicy(null), {
    daily_minutes: {},
    windows: [],
    sites: [],
    source_text: "",
    updated_ts: "",
  });
  assert.deepEqual(normalizePolicy("nope").daily_minutes, {});
});

test("normalizePolicy does not mutate its input", () => {
  const input = { daily_minutes: { default: 60 }, sites: [{ host: "x.com" }] };
  const snapshot = JSON.stringify(input);
  normalizePolicy(input);
  assert.equal(JSON.stringify(input), snapshot);
});

// ---- policyIsEmpty --------------------------------------------------------

test("policyIsEmpty", () => {
  assert.equal(policyIsEmpty({}), true);
  assert.equal(policyIsEmpty({ daily_minutes: {} }), true);
  assert.equal(policyIsEmpty({ daily_minutes: { default: 60 } }), false);
  assert.equal(policyIsEmpty({ sites: [{ host: "x.com" }] }), false);
});

// ---- normalizeUsage -------------------------------------------------------

test("normalizeUsage reads the general envelope", () => {
  const u = normalizeUsage({
    general: { used_ms: 30000, limit_ms: 60000, remaining_ms: 30000, blocked: false },
  });
  assert.deepEqual(u, {
    usedMs: 30000,
    limitMs: 60000,
    remainingMs: 30000,
    blocked: false,
    hasLimit: true,
  });
});

test("normalizeUsage treats null limit as unlimited", () => {
  const u = normalizeUsage({ general: { used_ms: 5000, limit_ms: null, remaining_ms: null } });
  assert.equal(u.hasLimit, false);
  assert.equal(u.limitMs, null);
  assert.equal(u.remainingMs, null);
});

test("normalizeUsage accepts a bare general object and bad input", () => {
  assert.equal(normalizeUsage({ used_ms: 1000, limit_ms: 2000 }).usedMs, 1000);
  assert.equal(normalizeUsage(null).usedMs, 0);
});

// ---- formatMinutes / formatMs --------------------------------------------

test("formatMinutes", () => {
  assert.equal(formatMinutes(0), "0m");
  assert.equal(formatMinutes(45), "45m");
  assert.equal(formatMinutes(60), "1h");
  assert.equal(formatMinutes(90), "1h 30m");
  assert.equal(formatMinutes(1440), "24h");
  assert.equal(formatMinutes(-10), "0m");
});

test("formatMs converts to minutes", () => {
  assert.equal(formatMs(90000), "2m"); // 90s = 1.5min -> rounds to 2m
  assert.equal(formatMs(5400000), "1h 30m"); // 90 min
});

// ---- normalizeTimeRequests ------------------------------------------------

test("normalizeTimeRequests shapes rows and drops id-less items", () => {
  const out = normalizeTimeRequests([
    { id: "treq_1", profile: "kid", requested_minutes: 30, reason: "homework", created_ts: "t" },
    { profile: "kid", reason: "no id" },
    { id: "treq_2", target_host: "g.com" },
  ]);
  assert.equal(out.length, 2);
  assert.deepEqual(out[0], {
    id: "treq_1",
    profile: "kid",
    target_host: null,
    requested_minutes: 30,
    reason: "homework",
    note: "",
    created_ts: "t",
  });
  assert.equal(out[1].target_host, "g.com");
  assert.equal(out[1].requested_minutes, null);
});

test("normalizeTimeRequests is total on garbage", () => {
  assert.deepEqual(normalizeTimeRequests(null), []);
  assert.deepEqual(normalizeTimeRequests("x"), []);
});
