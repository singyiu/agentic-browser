/* Unit tests for the dashboard's Redux requests store (static/store.js).
   Pure reducer/actions/selectors only — no DOM, no Redux runtime needed
   (the browser wiring in store.js is guarded behind `typeof window`).
   Run: node --test agent-backend/tests/frontend/ */

"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");

const {
  requestsReducer,
  actions,
  selectors,
  initial,
} = require("../../src/agent_backend/guardian/static/store.js");

const INIT = { type: "@@redux/INIT" };

// ---- action creators ----------------------------------------------------

test("actions.requestsLoading -> {type}", () => {
  assert.deepEqual(actions.requestsLoading(), { type: "requests/loading" });
});

test("actions.requestsLoaded carries pending/recent/at", () => {
  const p = [{ id: "a" }];
  const r = [{ id: "b" }];
  assert.deepEqual(actions.requestsLoaded(p, r, 7), {
    type: "requests/loaded",
    pending: p,
    recent: r,
    at: 7,
  });
});

test("actions.requestsFailed carries error/at", () => {
  assert.deepEqual(actions.requestsFailed("403", 9), {
    type: "requests/failed",
    error: "403",
    at: 9,
  });
});

test("actions.requestsReset -> {type}", () => {
  assert.deepEqual(actions.requestsReset(), { type: "requests/reset" });
});

// ---- reducer -------------------------------------------------------------

test("reducer seeds the initial state", () => {
  const s = requestsReducer(undefined, INIT);
  assert.equal(s.status, "idle");
  assert.deepEqual(s.pending, []);
  assert.deepEqual(s.recent, []);
  assert.equal(s.error, null);
  assert.equal(s.lastUpdated, 0);
});

test("reducer returns the same state for an unknown action", () => {
  const prev = requestsReducer(undefined, INIT);
  const next = requestsReducer(prev, { type: "nope" });
  assert.strictEqual(next, prev);
});

test("loading sets status but preserves pending/recent", () => {
  const prev = {
    status: "ready",
    pending: [{ id: "x" }],
    recent: [{ id: "y" }],
    error: null,
    lastUpdated: 5,
  };
  const next = requestsReducer(prev, actions.requestsLoading());
  assert.equal(next.status, "loading");
  assert.deepEqual(next.pending, [{ id: "x" }]);
  assert.deepEqual(next.recent, [{ id: "y" }]);
});

test("loaded stores pending/recent, clears error, stamps lastUpdated", () => {
  const prev = { ...initial, status: "loading", error: "old" };
  const next = requestsReducer(
    prev,
    actions.requestsLoaded([{ id: "1" }, { id: "2" }], [{ id: "3" }], 42),
  );
  assert.equal(next.status, "ready");
  assert.equal(next.pending.length, 2);
  assert.equal(next.recent.length, 1);
  assert.equal(next.error, null);
  assert.equal(next.lastUpdated, 42);
});

test("loaded copies the payload arrays (no shared reference)", () => {
  const pending = [{ id: "1" }];
  const next = requestsReducer(initial, actions.requestsLoaded(pending, [], 1));
  pending.push({ id: "2" }); // mutate the caller's array afterwards
  assert.equal(next.pending.length, 1); // state must be unaffected
});

test("loaded tolerates missing payload arrays", () => {
  const next = requestsReducer(initial, { type: "requests/loaded", at: 1 });
  assert.deepEqual(next.pending, []);
  assert.deepEqual(next.recent, []);
});

test("failed keeps the last-good pending/recent (no badge flicker)", () => {
  const prev = {
    status: "ready",
    pending: [{ id: "x" }, { id: "z" }],
    recent: [{ id: "y" }],
    error: null,
    lastUpdated: 5,
  };
  const next = requestsReducer(prev, actions.requestsFailed("network", 99));
  assert.equal(next.status, "error");
  assert.equal(next.error, "network");
  assert.deepEqual(next.pending, [{ id: "x" }, { id: "z" }]); // preserved
  assert.deepEqual(next.recent, [{ id: "y" }]);
  assert.equal(next.lastUpdated, 99);
});

test("reset returns the initial state", () => {
  const prev = {
    status: "ready",
    pending: [{ id: "x" }],
    recent: [],
    error: null,
    lastUpdated: 5,
  };
  const next = requestsReducer(prev, actions.requestsReset());
  assert.equal(next.status, "idle");
  assert.deepEqual(next.pending, []);
  assert.deepEqual(next.recent, []);
  assert.equal(next.lastUpdated, 0);
});

test("reducer never mutates the previous state", () => {
  const prev = Object.freeze({
    status: "ready",
    pending: Object.freeze([{ id: "x" }]),
    recent: Object.freeze([]),
    error: null,
    lastUpdated: 1,
  });
  // Would throw in strict mode if the reducer tried to mutate a frozen object.
  const next = requestsReducer(prev, actions.requestsLoaded([{ id: "n" }], [], 2));
  assert.notStrictEqual(next, prev);
  assert.equal(prev.status, "ready"); // untouched
  assert.deepEqual(prev.pending, [{ id: "x" }]); // untouched
  assert.equal(next.pending[0].id, "n");
});

// ---- selectors -----------------------------------------------------------

test("selectors read through the requests slice", () => {
  const root = {
    requests: {
      status: "ready",
      pending: [{ id: "a" }, { id: "b" }],
      recent: [{ id: "c" }],
      error: "boom",
      lastUpdated: 3,
    },
  };
  assert.equal(selectors.pendingCount(root), 2);
  assert.equal(selectors.pending(root).length, 2);
  assert.equal(selectors.recent(root).length, 1);
  assert.equal(selectors.status(root), "ready");
  assert.equal(selectors.error(root), "boom");
});

test("selectors.pendingCount is 0 on the initial slice", () => {
  assert.equal(selectors.pendingCount({ requests: initial }), 0);
});
