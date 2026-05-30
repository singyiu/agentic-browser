/* Redux state for the parent dashboard — a single `requests` slice tracking the
   pending + recently-decided access requests that drive the sidebar count badge.

   The dashboard has no build step, so Redux is loaded as a UMD <script> before this
   file (window.Redux). The reducer is pure and immutable (new objects only — see the
   project immutability rule); the browser store is created here and exposed on
   window.AegisStore for shell.js to dispatch into and subscribe to.

   The same file is require()-able by Node tests: the browser wiring is guarded behind
   `typeof window`, and the pure parts are exported via module.exports. */

(function () {
  "use strict";

  // Last-good pending/recent are deliberately retained on a failed refresh so a
  // transient poll error never flickers the badge to zero.
  const initial = {
    status: "idle", // idle | loading | ready | error
    pending: [],
    recent: [],
    error: null,
    lastUpdated: 0,
  };

  const actions = {
    requestsLoading: () => ({ type: "requests/loading" }),
    requestsLoaded: (pending, recent, at) => ({
      type: "requests/loaded",
      pending,
      recent,
      at,
    }),
    requestsFailed: (error, at) => ({ type: "requests/failed", error, at }),
    requestsReset: () => ({ type: "requests/reset" }),
  };

  function requestsReducer(state = initial, action = {}) {
    switch (action.type) {
      case "requests/loading":
        return { ...state, status: "loading" };
      case "requests/loaded":
        return {
          ...state,
          status: "ready",
          pending: [...(action.pending || [])],
          recent: [...(action.recent || [])],
          error: null,
          lastUpdated: action.at || 0,
        };
      case "requests/failed":
        // Keep the prior pending/recent (last-good); only flag the error.
        return {
          ...state,
          status: "error",
          error: action.error || "error",
          lastUpdated: action.at || 0,
        };
      case "requests/reset":
        return initial;
      default:
        return state;
    }
  }

  const selectors = {
    pending: (s) => s.requests.pending,
    pendingCount: (s) => s.requests.pending.length,
    recent: (s) => s.requests.recent,
    status: (s) => s.requests.status,
    error: (s) => s.requests.error,
  };

  // Browser wiring — skipped under Node (window undefined) and when the vendored
  // Redux failed to load. legacy_createStore avoids Redux 4.2's createStore
  // deprecation warning; combineReducers namespaces the slice under `requests`.
  if (typeof window !== "undefined") {
    if (window.Redux) {
      const create = window.Redux.legacy_createStore || window.Redux.createStore;
      const root = window.Redux.combineReducers({ requests: requestsReducer });
      window.AegisStore = { store: create(root), actions, selectors };
    } else {
      // eslint-disable-next-line no-console
      console.error("Aegis: Redux failed to load — pending-request badge disabled.");
    }
  }

  // Node test export (pure parts only; no DOM / Redux required).
  if (typeof module !== "undefined" && module.exports) {
    module.exports = { requestsReducer, actions, selectors, initial };
  }
})();
