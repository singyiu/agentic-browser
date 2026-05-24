# Aegis Design System

> The guardian layer that lets families browse the open web together — warm, calm, and reassuring rather than punitive.

## What is Aegis?

**Aegis** is a Claude-Max-backed agentic browser-control backend that classifies pages, enforces a parental whitelist, and lets a parent approve or block what their kid asks to unblock. It's the *guardian layer* on top of an `agentic-browser` fork.

The name comes from the mythical shield of Athena — protection, not prison. That metaphor drives every visual choice: warmth over alarm, conversation over enforcement, **shield not cage**.

### Who uses it?

There are two humans in the loop:

- **The parent / guardian** — sets the whitelist, reviews access requests, sets a PIN, and approves or rejects pages their kid wants to visit. They use the `/review` and `/setup` web UIs on their own machine.
- **The kid / teen** — browses on a Chromium build with the Aegis extension installed. When a page is blocked they see a block page and can request access with an optional note ("for homework", "it's just the search results").

A single guardian can govern several teens at once via `data/guardian_profiles.json` — each teen has their own token, whitelist, and pending-request stream, all reviewed from one parent screen.

### What are the surfaces?

| Surface | Who sees it | Where |
|---|---|---|
| **Block page** | Kid | Injected into the browser when classifier returns `block` |
| **Parent review UI** | Parent | `http://127.0.0.1:2947/review` |
| **Setup wizard** | Parent | `http://127.0.0.1:2947/setup` (first run; chooses PIN) |
| **CLI scripts** | Both | `launch-chromium.sh`, `launch-guardian.sh`, `install-guardian-service.sh` |

The backend itself is invisible — a FastAPI service on `:2947` with a metrics port on `:2948`, plus a stdio MCP server that drives Chromium over CDP. Everything *humans* touch is one of the four surfaces above.

## Sources

This design system was created **without an attached codebase or Figma file** — it's derived entirely from the product README the user provided in the brief. If you have the actual repo or any mockups, hand them over and we'll re-derive the visuals against ground truth. The current direction is a thoughtful first pass, not a recreation.

Things explicitly stated by the user, taken as gospel:

- Warm, earthy color palette
- Rounded corners
- Playful and warm brand voice

Everything else (type stack, spacing scale, motion, components) is a designer's first pass and open to iteration.

---

## CONTENT FUNDAMENTALS

Aegis sits between a parent and their kid in a moment of friction — a blocked page, a denied request, a request for trust. The copy has to absorb that friction without amplifying it.

### Voice

**Warm, playful, plain-spoken.** Like a parent who is on the kid's side and also not budging. Never institutional ("Access denied. Contact your administrator."), never punitive ("This site has been blocked."), never saccharine ("Oopsie! 🚫"). Aegis is the friendly bouncer who already knows your name.

### Person & tense

- **Address the reader as "you."** ("You'll get a ping when this is approved.")
- **The guardian is "we"** when it acts ("We sent your request to your parent.")
- **The parent is "your parent"** to the kid, never "the administrator" or "the user."
- **Present tense, active voice.** "Your parent is reviewing this." Not "This page is currently under review by the parental authority."

### Casing & punctuation

- **Sentence case everywhere** — buttons, headings, navigation. Never Title Case Like A Press Release.
- **One sentence per thought.** Periods are optional on single-line UI strings ("Request sent", "Waiting for your parent").
- **Em-dashes welcome** for the little asides — they keep things conversational.
- **No exclamation points** except on genuine wins ("Approved! Try the page again.").

### Emoji & ornament

**No emoji in production UI.** A single shield mark (the logo) is the only ornament. The warmth comes from color, type, and copy — not from 🚫🔒👀.

### Examples — write like this

> **Block page headline:** This one needs your parent's okay.
> **Sub:** We can ask them for you — add a note if it helps explain why.
>
> **Button:** Ask my parent
> **Secondary:** Check if approved
>
> **Setup wizard:** Pick a PIN only you'll remember. Four to eight digits — your kid never sees it.
>
> **Parent review header:** Three pages waiting on you.
> **Empty state:** Nothing pending. Quiet afternoon.

### Examples — not like this

> ~~Access Denied. This URL has been classified as inappropriate by your administrator.~~
> ~~SUBMIT REQUEST FOR PARENTAL REVIEW~~
> ~~🔒 Oopsie! That page is a no-no 🚫~~
> ~~Your request has been successfully transmitted to the designated guardian account.~~

### Microcopy patterns

- **Block reasons** read as plain categories, not codes: `adult content`, `graphic violence`, `not on the whitelist yet`.
- **Status strings** stay short: `Pending`, `Approved`, `Rejected`, `Waiting on your parent`.
- **Errors are human:** "Couldn't reach the guardian — make sure your parent's computer is on the same Wi-Fi." Not `ECONNREFUSED 127.0.0.1:2947`.
- **Time** is relative when fresh (`2 min ago`, `just now`) and absolute when stale (`Tue 3:42 PM`).

---

## VISUAL FOUNDATIONS

### The mood

**Warm kitchen, not server room.** Think the lighting of a late-afternoon living room — terracotta, ochre, oat, sage, deep cocoa. Surfaces are *paper*, not glass. Edges are rounded. Shadows are soft. Type has a serif voice for headings (it's a household, not a SaaS dashboard) and a humanist sans for body.

### Color

A small, deliberate palette. Five neutrals from cream to cocoa, three brand accents (terracotta, ochre, sage), and three semantics (allow / pending / block).

- **Cream** `oklch(0.97 0.012 80)` — page background. The whole product sits on warm paper.
- **Linen** `oklch(0.94 0.018 78)` — card and panel surfaces, one notch up from background.
- **Cocoa** `oklch(0.28 0.025 50)` — primary text. Brown, not black; pure black would feel clinical.
- **Stone** `oklch(0.55 0.018 60)` — secondary text and meta.
- **Terracotta** `oklch(0.66 0.14 38)` — the brand primary. Buttons, links, the shield.
- **Ochre** `oklch(0.78 0.13 75)` — accent / pending state.
- **Sage** `oklch(0.62 0.07 145)` — allow / approved state.
- **Brick** `oklch(0.52 0.16 28)` — block / reject state. Warmer than alert-red.

No bluish-purple anywhere. No #FFFFFF — use cream. No #000000 — use cocoa.

### Type

- **Display & headings:** *Instrument Serif* (Google Fonts) — italicizable serif with real warmth and one optical size. Used italic for headlines, roman for everything else.
- **Body & UI:** *Manrope* (Google Fonts) — geometric humanist sans with soft terminals. Pairs with a serif without competing.
- **Mono:** *JetBrains Mono* (Google Fonts) — for `curl` snippets, token previews, file paths.

Headings are *generous* (40–72px on the parent UI). Body is **16px** minimum. Line-height stays 1.5+ for body so it reads like prose, not a form.

### Spacing & layout

- **4 / 8 / 12 / 16 / 24 / 32 / 48 / 64 / 96** — a soft modular scale, no half-pixel values.
- **Inner padding** on cards is generous (24–32px) — never let copy touch a card edge.
- **Page gutters** are 32px on desktop. The parent review UI is centered in an 880px column; it's a reading surface, not a dashboard.

### Corners & borders

- **Cards:** `border-radius: 20px`. Big, friendly, not sharp.
- **Buttons & inputs:** `border-radius: 12px`. Pill-rounded on small chips (`999px`).
- **Borders:** hairline `1px` in `oklch(0.88 0.018 75)` — never harsh.

### Shadow & elevation

Two layers, both warm-tinted (not gray):

- **Soft:** `0 1px 2px oklch(0.4 0.04 60 / 0.06), 0 4px 16px oklch(0.4 0.04 60 / 0.06)` — resting cards.
- **Lifted:** `0 8px 24px oklch(0.4 0.04 60 / 0.10), 0 2px 6px oklch(0.4 0.04 60 / 0.08)` — modals, the active request in the review queue.

No drop-shadow on buttons. No inner shadows.

### Backgrounds

The default page background is plain **cream**. There is one optional "paper" texture — a barely-visible noise overlay at ~3% opacity — used only on the marketing-ish setup wizard and the block page. Cards are flat fills, never gradients. No full-bleed photography (this is a privacy product running on a parent's machine — no stock imagery).

The one exception: the brand shield mark, which may appear as a large watermark on the setup wizard hero.

### Motion

**Calm, never bouncy.**

- **Default easing:** `cubic-bezier(0.2, 0.7, 0.2, 1)` — a confident ease-out.
- **Duration:** 160ms for hovers, 220ms for state changes, 320ms for entering panels.
- **No spring physics.** No bounces. The product is reassuring, not exuberant.
- **One exception:** when a request flips to "Approved", the row gives a single subtle pulse of sage. Anything else feels like a toy.

### Hover & press

- **Buttons hover:** background darkens by ~6% (a `color-mix` with cocoa, not a separate token).
- **Buttons press:** scale to `0.98` for 80ms.
- **Cards hover:** elevation steps from Soft → Lifted; no color change.
- **Links hover:** underline appears (links don't carry a permanent underline in body copy).

### Transparency & blur

- **Used sparingly.** A 20-blur backdrop on the PIN-entry modal scrim, and that's about it.
- **No frosted-glass nav bars.** No semi-transparent cards. The system reads as opaque paper.

### Imagery tone

If a real photo is ever used (it usually isn't), it's warm-graded — slight orange lift in the shadows, no cool tones, never desaturated to b&w. But the default answer for "should this have a hero image?" is **no**.

### Fixed elements & layout rules

- The parent review header is sticky, but it's just the header — no floating action bars.
- The block page is centered vertically and horizontally in the viewport; it owns the whole tab.
- The setup wizard is a single column, max-width 520px.
- No sidebars in any web UI — this is not a dashboard product.

---

## ICONOGRAPHY

**Lucide** is the icon system — clean 1.5px stroke, rounded line caps, no fill. It matches the rounded-corner brand language and renders as inline SVG so it inherits color cleanly.

Icons are used **sparingly**: one per row in the review queue (status), one inline in each form label hint, and that's it. No icon-only buttons in the primary flows — buttons always carry a verb in text.

- **Source:** [`lucide.dev`](https://lucide.dev/) — loaded from CDN (`https://unpkg.com/lucide@latest`)
- **Sizes:** `16px` inline-with-text, `20px` button-leading, `24px` standalone in a status pill.
- **Stroke:** keep the default `1.5px` — do not thicken.
- **Color:** always inherit (`stroke: currentColor`).

**No emoji.** Not in product copy, not in error messages, not in empty states. The single illustrative element is the **shield mark** (in `assets/aegis-shield.svg`) and it is *the* logo — not decoration to be sprinkled around.

**Unicode used as iconography** only for: the round bullet `•` in meta separators, the en-dash `–` in ranges, the em-dash `—` in prose, arrow `→` on "next" links.

> ⚠️ Substitution flag: Lucide is being used as a stand-in until the codebase's own icon assets are attached. If Aegis ships its own SVG sprite, swap it in here.

---

## Index

| File | What's in it |
|---|---|
| `README.md` | This file — context, voice, visual foundations, iconography |
| `SKILL.md` | Cross-compatible Agent Skill entry point |
| `colors_and_type.css` | All design tokens — color, type, spacing, radius, shadow, motion |
| `fonts/` | Webfont files (currently Google Fonts CDN — see substitution note) |
| `assets/` | Logo, shield mark, paper texture, generic illustration placeholders |
| `preview/` | Cards rendered into the Design System tab |
| `ui_kits/parent-review/` | Parent's `/review` queue + decision flow |
| `ui_kits/kid-block-page/` | The blocked-page experience the kid sees in-browser |
| `ui_kits/setup-wizard/` | First-run PIN-creation wizard |

## Font substitution note

No font files were provided. The current stack pulls **Instrument Serif**, **Manrope**, and **JetBrains Mono** from Google Fonts. If Aegis has a licensed display face we should use instead, drop the files into `fonts/` and I'll re-wire `colors_and_type.css` to load them locally.

## Caveats

- No codebase / Figma was attached, so every visual decision is a first pass. The brief said "warm, earthy, rounded, playful, warm voice" — that's the spine. Everything else is open.
- No real product screens exist yet, so the UI kits recreate the *described* flows (block page → request → parent review → approve). Real implementation may differ.
- Iconography is Lucide (CDN) as a substitution — flag noted in the ICONOGRAPHY section.
