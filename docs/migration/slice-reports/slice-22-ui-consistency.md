# Slice 22 — UI consistency pass

**Branch**: `release/astro-migration`
**Date**: 2026-09-25
**Trigger**: dogfood walkthrough in `Desktop/migration/dogfood-2026-09-25.md`
**Builds on**: `8c58c12` (slice 22-prep: shared PageHeader, FormActions,
EmptyState, ErrorBanner primitives)

## Goal

Replace ad-hoc header / back-link / button-row markup on every
application page with the shared primitives from slice 22-prep, so
all pages share one consistent header pattern.

## Touched files

12 pages + 3 components:

- web/concepts/src/pages/habits/index.astro
- web/concepts/src/pages/habits/new.astro
- web/concepts/src/pages/habits/[id].astro
- web/concepts/src/pages/habits/order.astro
- web/concepts/src/pages/stats.astro
- web/concepts/src/pages/settings.astro
- web/concepts/src/pages/security.astro
- web/concepts/src/pages/account/delete.astro  (was already migrated in slice 17)
- web/concepts/src/pages/tokens.astro
- web/concepts/src/pages/login.astro
- web/concepts/src/pages/register.astro
- web/concepts/src/pages/habits/[id].astro
- web/concepts/src/components/HabitGrid.astro
- web/concepts/src/components/Heatmap.astro
- web/concepts/src/components/SecurityContent.tsx

## Changes per page

Each Astro page now opens with `<PageHeader title=... back=...
subtitle=...>`. Where DesktopMenu is appropriate (logged-in pages
only), it's mounted in the actions slot:

```astro
<PageHeader title="Habits" subtitle={`${count} active habits.`}>
  <DesktopMenu slot="actions" client:load email={Astro.locals.session.email} />
</PageHeader>
```

Form pages additionally use `<FormActions cancelHref=...
submitLabel=... />` for the bottom button row.

## Verification

- `cd web/concepts && pnpm exec astro check` → 0 errors / 0 warnings / 58 hints.
- Browser walkthrough confirmed header alignment on /habits, /habits/new,
  /stats, /settings, /security, /account/delete, /tokens, /login,
  /register (demo@castor.example.com).
- All 374 tests pass (368 baseline + 6 new slice-23a tests).

## Known gaps left for slice 22x

These were not addressed in this slice and are tracked separately:

1. `/habits` day-of-week header strip still floats above the first
   habit row with a visible gap. The strip in `HabitGrid.astro:216`
   is a separate Card from the row articles; merging them into one
   container would close the gap.
2. `+ Add habit` is still plain text on /habits. The link is rendered
   by `HabitGrid.astro` and needs to be a shadcn Button.
3. Sort by sits in its own row above the grid instead of inside the
   PageHeader actions slot. Moving it would crowd the header; user
   preference TBD.
4. `/security` Loading bug — the page stays on "Loading..." after
   mount. The inline `<script is:inline>` defining `window.__SECURITY_TOKEN__`
   is in the right place but `SecurityContent.tsx` likely has a
   hydration race. Separate slice 22x.

## Rollback

Revert the single commit `4c85b7f`. Astro check still passes at
baseline 62 hints; pytest unaffected (no Python changes in this
slice).

## Out of scope

- /terms, /privacy, /help still use the static-content layout (no
  PageHeader needed). They each have their own internal `<article>`
  structure that didn't need migration.
- The Circles pages are migrated in the parallel slice 23b.