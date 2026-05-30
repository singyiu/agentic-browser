/* Unit tests for the Activity-page rule-builder pure logic (static/rules.js).
   Pure functions only — no DOM, no fetch (the browser glue in rules.js is guarded
   behind `typeof window`). Run: node --test agent-backend/tests/frontend/ */

"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");

const {
  KINDS,
  hostOf,
  seedValue,
  normalizeEntry,
  buildApplyPayloads,
  normalizeSuggestions,
} = require("../../src/agent_backend/guardian/static/rules.js");

// ---- KINDS ----------------------------------------------------------------

test("KINDS lists the four rule kinds in order", () => {
  assert.deepEqual(
    KINDS.map((k) => k.id),
    ["exact", "wildcard", "nl", "ai"],
  );
  for (const k of KINDS) assert.equal(typeof k.label, "string");
});

// ---- hostOf ---------------------------------------------------------------

test("hostOf strips scheme, www, path and query; lowercases", () => {
  assert.equal(hostOf("https://www.YouTube.com/watch?v=1"), "youtube.com");
  assert.equal(hostOf("http://example.org"), "example.org");
  assert.equal(hostOf("https://sub.example.co.uk/a/b?c#d"), "sub.example.co.uk");
  assert.equal(hostOf("ftp://files.test/x"), "files.test");
});

test("hostOf accepts a bare host with no scheme", () => {
  assert.equal(hostOf("example.com"), "example.com");
});

test("hostOf returns '' for non-host input", () => {
  assert.equal(hostOf("not a url"), "");
  assert.equal(hostOf(""), "");
  assert.equal(hostOf(null), "");
  assert.equal(hostOf("localhost"), ""); // no dot -> not a domain
});

// ---- seedValue ------------------------------------------------------------

test("seedValue derives the box contents per kind", () => {
  const ev = { url: "https://www.x.com/a?b=1" };
  assert.equal(seedValue(ev, "exact"), "https://www.x.com/a?b=1"); // full page URL, verbatim
  assert.equal(seedValue(ev, "wildcard"), "x.com/*"); // broaden to the whole host
  assert.equal(seedValue(ev, "nl"), "");
  assert.equal(seedValue(ev, "ai"), "");
});

test("seedValue exact keeps the full path + query (blocks one specific page)", () => {
  const url = "https://www.youtube.com/results?search_query=How+to+kill";
  assert.equal(seedValue({ url }, "exact"), url);
  assert.equal(seedValue({ url }, "wildcard"), "youtube.com/*");
});

test("seedValue falls back to url_key and tolerates missing data", () => {
  assert.equal(seedValue({ url_key: "y.com/z" }, "exact"), "y.com/z");
  assert.equal(seedValue({}, "exact"), "");
  assert.equal(seedValue(null, "wildcard"), "");
});

// ---- normalizeEntry -------------------------------------------------------

test("normalizeEntry trims and collapses whitespace", () => {
  assert.deepEqual(normalizeEntry("  hello world  "), {
    ok: true,
    value: "hello world",
    error: "",
  });
  assert.deepEqual(normalizeEntry("a\n\tb   c"), { ok: true, value: "a b c", error: "" });
});

test("normalizeEntry rejects empty / whitespace-only", () => {
  assert.equal(normalizeEntry("").ok, false);
  assert.equal(normalizeEntry("   ").ok, false);
  assert.equal(normalizeEntry(null).ok, false);
});

test("normalizeEntry enforces the 512-char cap", () => {
  assert.equal(normalizeEntry("x".repeat(512)).ok, true);
  assert.equal(normalizeEntry("x".repeat(513)).ok, false);
});

test("normalizeEntry rejects control characters", () => {
  // BEL (0x07) is not whitespace, so it survives the collapse and must trip the printable guard.
  assert.equal(normalizeEntry("bad" + String.fromCharCode(7) + "char").ok, false);
});

// ---- buildApplyPayloads ---------------------------------------------------

test("buildApplyPayloads makes one payload per selected profile", () => {
  assert.deepEqual(buildApplyPayloads("youtube.com", ["alice"]), [
    { entry: "youtube.com", profile: "alice" },
  ]);
  assert.deepEqual(buildApplyPayloads("youtube.com", ["alice", "bob", "global"]), [
    { entry: "youtube.com", profile: "alice" },
    { entry: "youtube.com", profile: "bob" },
    { entry: "youtube.com", profile: "global" },
  ]);
});

test("buildApplyPayloads normalizes the entry once", () => {
  assert.deepEqual(buildApplyPayloads("  spaced  out  ", ["alice"]), [
    { entry: "spaced out", profile: "alice" },
  ]);
});

test("buildApplyPayloads returns [] for an invalid entry or empty selection", () => {
  assert.deepEqual(buildApplyPayloads("", ["alice"]), []);
  assert.deepEqual(buildApplyPayloads("youtube.com", []), []);
  assert.deepEqual(buildApplyPayloads("youtube.com", null), []);
});

test("buildApplyPayloads dedupes and skips blank profile names", () => {
  assert.deepEqual(buildApplyPayloads("x.com", ["alice", "alice", "", "  "]), [
    { entry: "x.com", profile: "alice" },
  ]);
});

// ---- normalizeSuggestions -------------------------------------------------

test("normalizeSuggestions keeps valid items in order", () => {
  const out = normalizeSuggestions([
    { kind: "wildcard", value: "game.test/*", reason: "lots of gaming" },
    { kind: "nl", value: "violent games", reason: "" },
  ]);
  assert.equal(out.length, 2);
  assert.deepEqual(out[0], { kind: "wildcard", value: "game.test/*", reason: "lots of gaming" });
  assert.equal(out[1].kind, "nl");
});

test("normalizeSuggestions drops malformed items", () => {
  const out = normalizeSuggestions([
    { kind: "exact", value: "ok.test", reason: "r" },
    { kind: "exact", reason: "no value" },
    "not an object",
    null,
    { value: "", reason: "empty" },
  ]);
  assert.equal(out.length, 1);
  assert.equal(out[0].value, "ok.test");
});

test("normalizeSuggestions defaults an unknown kind to content", () => {
  const out = normalizeSuggestions([{ kind: "weird", value: "z.test", reason: "r" }]);
  assert.equal(out[0].kind, "content");
});

test("normalizeSuggestions clamps to 8 and trims reason", () => {
  const many = Array.from({ length: 20 }, (_, i) => ({
    kind: "nl",
    value: "topic " + i,
    reason: "y".repeat(400),
  }));
  const out = normalizeSuggestions(many);
  assert.equal(out.length, 8);
  assert.equal(out[0].reason.length, 300);
});

test("normalizeSuggestions returns [] for a non-array", () => {
  assert.deepEqual(normalizeSuggestions(null), []);
  assert.deepEqual(normalizeSuggestions("nope"), []);
});
