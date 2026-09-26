# Slice 16 — Private Circles drawer entry + shadcn Checkbox for Display card

**Branch**: `release/astro-migration` (parent `3cbf9b0` from slice 15)
**Date**: 2026-09-24

This slice landed two unrelated UX gaps that were both blocked by
prior inspection work:

1. **No desktop entry point for Circles** — verified end-to-end via
   `demo@castor.example.com` (slice 15 wrap-up) that the mobile
   `MoreSheet.tsx` had a Circles link but the desktop `DesktopMenu.tsx`
   did not. User confirmed (out-of-band Telegram message) that
   Circles belongs in its **own "Sharing" section** in both drawers,
   not buried under Tools or Data.

2. **Display card checkboxes were plain HTML** — `/settings` showed
   Display preferences with raw `<input type="checkbox">` inputs and
   ~200 lines of dual-mount state glue (cookie + DB sync) in
   `SettingsClient.tsx`. The shadcn Checkbox component had never
   been installed, so the page was the last place on the settings
   surface using non-shadcn primitives.

## Changes

### 16a. Circles in its own Sharing section (commit `3cbf9b0`)

- `web/concepts/src/components/DesktopMenu.tsx` — new `Sharing`
  group between Tools and Account, single entry "Private Circles"
  with `Users` icon (lucide-react), click → `closeAndNavigate('/circles')`.
- `web/concepts/src/components/MoreSheet.tsx` — extracted Circles
  from the Data group into a new Sharing section, mirrored desktop
  structure.
- Verified end-to-end on `demo@castor.example.com`: clicked Circles
  in both desktop drawer and mobile sheet → drawer closes → navigates
  to `/circles` → list renders → click "Demo Family" → `/circles/1`
  shows members, shared habits, recent activity, invites.

### 16b. shadcn Checkbox for Display card (commit `a82011a`)

- Installed `@radix-ui/react-checkbox@1.3.11` (added to
  `web/concepts/package.json` and `pnpm-lock.yaml`).
- New file `web/concepts/src/components/ui/checkbox.tsx` — canonical
  shadcn Checkbox wrapping `@radix-ui/react-checkbox` with the
  standard shadcn styling (`data-[state=checked]:bg-primary`,
  `data-[state=checked]:text-primary-foreground`).
- New file `web/concepts/src/components/DisplayCard.tsx` — entire
  Display card body (Show streak + Show total + Save button +
  status indicator) as a self-contained React island. No more
  cookie + DB dual-mount glue in `SettingsClient.tsx`.
- `web/concepts/src/pages/settings.astro` — `<DisplayCard client:load />`
  replaces the previous inline-form implementation.

**Bonus bug fix**: the old DisplayPrefBinder's rollback path on
PUT failure didn't roll back the `habit_${key}` cookie, so a failed
DB write could leave the UI showing one value while the cookie
read said another. The new DisplayCard rolls back state + cookie
together.

## Verification

- `pnpm exec astro check` → 0 errors / 0 warnings / 62 hints.
- Visually verified: Radix `aria-checked="true"` + `data-state="checked"`
  on the toggled checkbox; CSS class `bg-primary text-primary-foreground`
  applied.
- Database state round-trip: toggled Show streak off→on →
  `PUT /api/v1/user-configs {"show_streak": true}` →
  toggled Show total off→on → `{"show_streak": true, "show_total": true}` →
  toggled Show total off → `{"show_streak": true, "show_total": false}`.
- All previous tests pass: 368 passed + 12 skipped.

## What this slice does NOT touch

- No backend changes — all display prefs are stored in the existing
  `user_configs` table and read/written via the BFF route added in
  slice 15 (`/api/v1/user-configs`).
- No parity matrix rows changed status — Circles was already ✅ in
  slice 13 (added via audit), and Display prefs 12.6 was already ✅
  from slice 12. This slice is **stylistic consistency + UX entry
  gap**, not functional parity.
