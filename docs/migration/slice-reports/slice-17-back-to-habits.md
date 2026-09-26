# Slice 17 — "Back to habits" link on deep pages

**Branch**: `release/astro-migration` (parent `a82011a` from slice 16)
**Date**: 2026-09-24

## Trigger

A quick survey of the 37 Astro pages found that 29 of them had no
way to get back to the main `/habits` grid once the user navigated
away. Pages like `/tokens`, `/account/delete`, `/habits/new` are
deep destinations where users typically arrive via a settings menu
click, then have no breadcrumb back to the primary habit view.

## Scope (deliberately small)

User asked for the minimum surface that mattered. Excluded by design:

- `/help` — public page (no auth); "back to habits" is wrong here.
- `/security` — already has a "← Back to settings" link inside the
  React island (verified).
- `/login`, `/reset-password`, `/verify-reset`, `/register`,
  `/account/delete/confirm` — pre-auth pages where "back to habits"
  doesn't apply.

The three pages modified are the only deep authenticated pages
that:

1. Have no other breadcrumb path back to `/habits`.
2. Are first-tier deep destinations (not nested subpages — those
   have their own context).

## Pattern (matches `/settings.astro`)

```astro
<a href="/habits"
   class="hidden sm:inline-flex items-center text-sm text-muted-foreground hover:text-foreground transition-colors">
  ← Back to habits
</a>
```

`hidden sm:inline-flex` mirrors `/settings.astro` so the link
disappears on small screens (the bottom nav handles "back" there).
The `text-muted-foreground hover:text-foreground` matches Tailwind
shadcn defaults.

## Files modified

- `web/concepts/src/pages/tokens.astro` — header flex with title
  block (h1 + description paragraph) on the left and the link
  top-right. Description paragraph preserved below h1.
- `web/concepts/src/pages/account/delete.astro` — header flex.
- `web/concepts/src/pages/habits/new.astro` — header flex; tagline
  ("One small step at a time") moved below the header to preserve
  visual rhythm.

## Verification

- `pnpm exec astro check` → 0 errors / 0 warnings / 62 hints.
- Manual browser click on `/tokens` link → navigated to `/habits`.
- Pytest baseline unchanged: `tests/test_slice{1,11,12}*.py` →
  36 passed + 6 skipped (no backend changes).

## What this slice does NOT touch

- No backend changes.
- No BFF route changes.
- No parity matrix rows changed status (navigation chrome is
  outside the parity-matrix scope).
