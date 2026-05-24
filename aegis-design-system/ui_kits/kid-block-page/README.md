# UI Kit · Kid Block Page

The full-tab page injected by the Aegis extension when the classifier returns `block` (or when a page isn't on the whitelist). This is the kid's primary touchpoint with the guardian — it has to absorb the rejection without feeling punitive.

## Components

- `<BlockApp />` — entry point. Holds the three states: `idle` (blocked, no request yet), `requesting` (composing a note), `sent` (waiting), `approved` (parent said yes).
- `<BlockedHero />` — the shield mark, the headline, and the URL/reason pair.
- `<RequestComposer />` — the inline "ask my parent" form (note + submit).
- `<PendingState />` — what shows after submitting; includes the "Check if approved" button.
- `<ApprovedState />` — the rare happy state; sage tint, "try the page again" button.

## Behavior notes

- The page **owns the whole tab** — viewport-centered, max width 560px column.
- All transitions are 320ms ease-out — no bounces. A blocked page is a quiet moment, not a surprise.
- The "Check if approved" button polls `/access-request?url=…` in production. Here it just flips state after a delay.
- The Aegis mark is the only ornament. No 🚫. No 🔒.
