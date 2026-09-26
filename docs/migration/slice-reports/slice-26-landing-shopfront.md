# Slice 26 — Landing page shop front

**Branch**: `release/astro-migration`
**Date**: 2026-09-25
**Trigger**: User request — turn the existing
developer-facing `index.astro` (which showed a "Backend
connection" card as its second-most-prominent element) into a
proper marketing shop front for Castor.

## Content shape

Six sections, single-column, centered reading flow
(`max-w-2xl`):

1. **Hero** — wordmark + tagline + subtitle + two CTAs.
2. **What it does** — three feature cards.
3. **Why people pick Castor** — three value-prop items.
4. **What people make with it** — three illustrative examples.
5. **On self-hosting** — one paragraph.
6. **Footer** — small monospace server-status line + Help /
   Terms / Privacy links.

## Brand voice

The hero tagline **"Dam good habits."** is a deliberate brand
pivot — punchy, confident, slightly irreverent. The rest of the
page stays compassionate and clear to balance the voice: the
hero carries the personality, the supporting copy carries the
reassurance.

The subtitle is intentionally short —
"A self-hosted habit tracker for people who want their data to
stay theirs." — a single sentence with one technical term
("self-hosted"). Anyone who knows what that means is the target
audience; everyone else can still read it.

## Server status: moved out of the hero

The previous page rendered the `/health` check result in a card
directly under the wordmark. That's operational telemetry, not
landing-page content. It now lives in a small (12px) monospace
line in the footer, visible to ops-minded visitors but invisible
to everyone else.

## CTA buttons — bug fix

First draft used `<Button asChild>` (Radix Slot pattern) wrapping
`<a>`. The same gotcha slice 22x hit earlier on `/habits`:
`<Button asChild>` inside a `.astro` file does **not** merge its
classes through to the child `<a>`. Both CTAs rendered as plain
text links with no padding, no background — confirmed by
`getComputedStyle` returning `bgColor: rgba(0,0,0,0)` and
`padding: 0px`.

Fix: replaced the shadcn wrapper with shadcn's actual button
classes inlined on the `<a>` directly. Two classes:
- Primary (filled): `bg-primary text-primary-foreground
  hover:bg-primary/90 h-11 px-6`
- Secondary (outlined): `border border-input bg-background
  hover:bg-accent h-11 px-6`

DOM re-check confirms `bg: oklch(0.55 0.16 162)` (the brand
green) and `padding: 0px 24px` after the fix. Dropped the unused
`Button` import.

## Verification

- `astro check`: 0 errors / 0 warnings / 58 hints (108 files).
- Live DOM inspection on `/`:
  - Hero `<h1>` at `x=456` (24px into a 672-px column centered
    on a 1536-px viewport) — standard single-column marketing
    layout, content left-aligned within the column.
  - Both CTAs render as `display: flex` with non-transparent
    backgrounds and 24-px horizontal padding.
  - Footer status line at `font-size: 12px` in monospace,
    `oklch(0.18 0.02 240 / 0.72)` muted-foreground color.
- Vision-model walk confirms hero is clean (no Backend
  connection card in the hero region), CTAs are real buttons,
  footer status is small and unobtrusive.

## Files touched

- `web/concepts/src/pages/index.astro` — rewritten as marketing
  shop front.
