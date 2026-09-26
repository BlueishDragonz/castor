# Slice 26d — /help restructured + back-button/H1 alignment

**Branch**: `release/astro-migration`
**Date**: 2026-09-25
**Trigger**: Review pass on the three public legal/info
pages. /help had its prose in one giant card and read as a
wall of text. /terms and /privacy had the same problem, plus
their 'back to home' footer links lived at the bottom of the
card where they're easy to miss.

## Two distinct fixes

### 1. Restructure /help into discrete cards (the big one)

The page used to be: PageHeader → one card containing all the
content as continuous prose. That mixed Quick links, five
FAQ Q&As, and a 'Need more help?' callout into one
indistinguishable block.

New layout, top-to-bottom:

  - PageHeader (back arrow + H1 + subtitle)
  - **Intro card** — one paragraph of context
  - **Quick links card** — 2×2 grid of clickable resource tiles
    (each tile: bold name + short description)
  - **Frequently asked questions card** — five native
    `<details>`/`<summary>` collapsible Q&As with a `▾` chevron
    that rotates 180° when open. Native HTML element = no JS
    needed, accessible by default, works without hydration.
  - **Need more help? card** — short callout with the
    issue-tracker link

Each top-level section now reads as its own panel. Eye can scan
headers and skip to the relevant card.

### 2. PageHeader: back arrow aligned with H1 (app-wide fix)

The back button + H1 title were visually offset by 11px on every
page using `PageHeader`. The user flagged this explicitly.

Root cause: `<Button asChild>` wrapping `<a>` in an `.astro` file
doesn't merge Radix Slot classes — same gotcha as slices 22x and
26. The button rendered as inline text instead of a 36×36
square. With the title row containing both an H1 and a subtitle
paragraph, `items-center` on the parent flex container centered
the small button against the *whole* title block (H1 +
subtitle) — putting it visually below the H1 alone.

Fix:

  - Replaced `<Button asChild variant="ghost" size="icon">` with
    a plain `<a>` carrying shadcn's button classes inline
    (`h-8 w-8 rounded-md hover:bg-accent ...`).
  - Restructured the header so the back button + H1 share a
    single flex row, and the subtitle drops to its own line
    below (indented by `ml-11` to align under the title text,
    not under the back button).
  - The back button is `h-8 w-8` (32×32) — matches the H1's
    natural line-height so the centers align at 0px offset.

This is a shared primitive change, so the fix benefits every
authenticated page that uses `PageHeader` too (habits, circles,
settings, etc.).

## Bug caught mid-flight

Initial draft of `help.astro` passed `style="border-radius: ..."`
to shadcn `<Card>` components. shadcn's `<Card>` types `style`
as `JSX.CSSProperties`, not a string — TypeScript flagged 4
errors. Card already has `rounded-2xl` baked into its default
classes, so the inline style was redundant. Dropped the
`style=` attribute everywhere.

## Verification

- `astro check`: 0 errors / 0 warnings / 58 hints (108 files).
- `pnpm exec astro check` clean on all 4 changed files.
- Programmatic alignment check on `/help`:
  - Back button: `top=48, bottom=80, h=32, center=64`
  - H1: `top=48, bottom=80, h=32, center=64`
  - **vertical offset: 0px** (was 11px before this slice)
- Vision walk confirms back arrow + title share the same
  horizontal line, subtitle indented under title text.

## Files touched

- `web/concepts/src/components/PageHeader.astro` — replaced
  Radix Slot wrapper with inline `<a>`, restructured header
  layout so subtitle drops below the back+H1 row.
- `web/concepts/src/pages/help.astro` — restructured into 5
  cards (intro / quick links / FAQ / need help) with native
  collapsible FAQs.
- `web/concepts/src/pages/terms.astro` — added PageHeader at
  top with back button.
- `web/concepts/src/pages/privacy.astro` — same as terms.
