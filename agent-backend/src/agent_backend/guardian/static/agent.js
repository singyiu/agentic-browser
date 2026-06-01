// Guardian "Agent" page — the flagship conversational assistant (default landing).
//
// Stub: wires the nav/route entry point so the section mounts cleanly. The full chat UI
// (transcript, composer, proposal cards, suggestions, version bar) lands in the next change.
(function () {
  "use strict";

  function load() {
    const root = document.getElementById("agent-root");
    if (!root || root.dataset.ready) return;
    root.dataset.ready = "1";
    root.textContent = "Agent assistant — loading…";
  }

  window.AegisAgent = { load: load };
})();
