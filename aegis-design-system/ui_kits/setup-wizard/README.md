# UI Kit · Setup Wizard

First-run web UI at `/setup`. A short, single-column flow that helps a parent pick a 4–8 digit PIN before they can review requests. The PIN is salt-hashed to `data/guardian_admin.json`; the kid's browser never sees it.

## Components

- `<SetupApp />` — orchestrates the four steps.
- `<StepIndicator />` — a thin progress rail at the top of the column.
- `<StepWelcome />` — the warm intro screen with the shield watermark.
- `<StepChoosePin />` — first PIN entry (`PinCells`).
- `<StepConfirmPin />` — re-enter to confirm.
- `<StepDone />` — confirmation + "Go to review" handoff.
- `<PinCells />` — the editable PIN field (4–8 cells, expandable).

## Notes

- Column width is **520px max** — this is a hand-held flow, not a dashboard.
- The PIN is **never echoed back** after step 2; if the parent forgets it, they have to delete `data/guardian_admin.json` and re-run the wizard. The wizard says so, gently.
- The shield mark is shown subtly behind the welcome step at ~6% opacity, big.
