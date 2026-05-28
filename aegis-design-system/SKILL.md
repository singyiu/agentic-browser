---
name: aegis-design
description: Use this skill to generate well-branded interfaces and assets for Aegis, either for production or throwaway prototypes / mocks. Contains essential design guidelines, colors, type, fonts, assets, and UI kit components for prototyping the guardian's parent and kid surfaces.
user-invocable: true
---

# Aegis design

Aegis is a Claude-Max-backed guardian layer on top of a Chromium fork — it classifies pages, enforces a parental whitelist, and lets a parent approve what their kid asks to unblock. The brand is **a shield, not a cage**: warm, earthy, rounded, playful, and never punitive.

Read `README.md` for the full context — voice, visual foundations, iconography — and explore the other files in this skill before designing anything.

## Quick orientation

- **Tokens:** `colors_and_type.css` — all color / type / spacing / radius / shadow / motion variables. Import this at the top of any new file: `<link rel="stylesheet" href="colors_and_type.css">`.
- **Shared UI styles:** `ui_kits/shared.css` — buttons, inputs, pills, cards. Link alongside `colors_and_type.css`.
- **Brand mark:** `assets/aegis-shield.svg` and `assets/aegis-wordmark.svg`.
- **Card previews:** `preview/*.html` — one card per token cluster, useful as a quick visual lookup.
- **UI kits:** `ui_kits/parent-review/`, `ui_kits/kid-block-page/`, `ui_kits/setup-wizard/`. Each has an `index.html` plus React JSX components you can copy-paste into new designs.

## What to do when invoked

If the user invokes this skill with no other guidance, ask them what they want to build or design and ask short clarifying questions (which surface? parent, kid, or new flow? prototype or production?). Then act as an expert designer who outputs **HTML artifacts** for prototypes / mocks, or **production-equivalent code** for the real product.

Always copy the brand assets you need (`assets/aegis-shield.svg` etc.) into the artifact rather than referencing them from outside the skill folder. Always link `colors_and_type.css` (and `ui_kits/shared.css` if you're using buttons / pills / cards).

## Visual non-negotiables

- **Color:** cream `oklch(0.972 0.012 80)` page, cocoa `oklch(0.28 0.025 50)` text, terracotta `oklch(0.66 0.14 38)` primary action. No `#FFFFFF`, no `#000000`, no bluish-purple gradients.
- **Type:** Source Serif 4 (display, claude.ai-adjacent) + Geist (body / UI) + Geist Mono (code). All from Google Fonts.
- **Radius:** 20px on cards, 12px on buttons/inputs, 999px on pills.
- **Motion:** 160 / 220 / 320ms, `cubic-bezier(0.2, 0.7, 0.2, 1)`. No springs. No bounces.
- **No emoji.** The shield mark is the only ornament.

## Voice non-negotiables

Warm, playful, plain-spoken. "We can ask them for you" — not "Submit request for parental review". Sentence case. "You" for the reader; "your parent" / "we" for the guardian. Read the CONTENT FUNDAMENTALS section of `README.md` before writing any copy.

## Surfaces & components

When asked to design a flow, start from the closest existing kit:

| Surface | Kit | Use when |
|---|---|---|
| Parent review queue | `ui_kits/parent-review/` | Anything on the parent's `/review` page |
| Kid block page | `ui_kits/kid-block-page/` | Anything injected by the extension when a page is blocked |
| Setup wizard | `ui_kits/setup-wizard/` | First-run / PIN creation / settings |

Copy components (`Brand.jsx`, `RequestRow.jsx`, `PinCells.jsx`, etc.) rather than redrawing them.

## Substitution notes

- **Fonts** are loaded from Google Fonts CDN — no licensed files were provided. If Aegis ships its own display face, drop the `.woff2` files into `fonts/` and rewire `colors_and_type.css`.
- **Icons** use Lucide via CDN. If Aegis ships its own sprite, replace the Lucide references.
- **Logo** is a designer's first-pass shield SVG, not a final brand mark.
