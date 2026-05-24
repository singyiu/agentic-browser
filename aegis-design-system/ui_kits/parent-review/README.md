# UI Kit · Parent Review

The `/review` web UI that lives on the parent's machine. A reading-friendly, centered column where the parent works through pending access requests and edits the whitelist.

## Components

- `<ReviewApp />` — the whole page (header, queue, decision modal). Entry point in `index.html`.
- `<TopBar />` — sticky header with the Aegis mark, teen-filter chips, and a small "PIN locked" indicator.
- `<EmptyState />` — "Nothing pending. Quiet afternoon."
- `<RequestRow />` — a single pending request card with note, source teen, and approve / reject actions.
- `<DecisionPanel />` — the editable "allow" field that pops open from a row. Lets the parent broaden the entry (URL → wildcard → topic) before approving.
- `<HistoryItem />` — a recently-approved or recently-rejected row, dimmer than pending.
- `<Brand />` — the Aegis shield + wordmark.

## Notes

- The UI is **one centered column, max 880px**. No sidebar — this is not a dashboard.
- The decision panel slides down from inside a request row; it doesn't open a modal. Lower friction.
- Approving an entry **always** writes to that teen's whitelist only. Multi-teen support is shown by the teen-filter chips and the per-row teen label.
- All copy follows the brand voice — playful and warm, never institutional.
