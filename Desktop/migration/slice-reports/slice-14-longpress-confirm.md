# Slice 14 — Long-press notes wire-up confirmation (audit follow-up)

**Branch**: `release/astro-migration` (parent `70dcb5d` from Slice 13)

**Date**: 2026-09-24

## Trigger

Slice 13's bulk audit ran `audit_parity_matrix.py` and reported row
2.13 (Daily note on habit, long-press) as 🟡 stale-evidence — its
files (`HabitNoteDialog.tsx`, `HabitNoteRow.tsx`) existed but the
gesture helper to wire them to the grid was presumed missing. Slice
14 (the very next slice candidate) **was meant to build that helper**.

## Finding

Slice 14 inspection: the helper is **already implemented** in
`HabitGrid.astro:463-523`. Tracing the full event chain:

1. Tick cell rendered with `data-long-press-delay` attribute
   (HabitCheckBox wrapper).
2. `HabitGrid.astro:489-522` listens for `mousedown` /
   `touchstart` / `contextmenu` on those cells.
3. After 200ms hold (or right-click), emits a
   `castor:tick-longpress` CustomEvent on `document`, with
   `{habitId, date, done}` detail.
4. The emitted event's click handler also swallows the next click
   so the underlying `<button>` doesn't fire its toggle form.
5. `HabitNoteRow.tsx:72-73` adds a `document.addEventListener`
   for `castor:tick-longpress` and pops the `HabitNoteDialog`.
6. `HabitNoteDialog` auto-saves the note every ~700ms via
   `/api/v1/habits/{id}/notes`.

End-to-end works. The matrix evidence for row 2.13 was simply
incomplete — it described the missing pieces (the dialog/row files)
but didn't reflect that they were already wired.

## What changed

- `Desktop/migration/parity-matrix.md` row 2.13: 🟡 → ✅ with new
  evidence line describing the full event chain.
- `Desktop/migration/parity-matrix.md` summary: bullet-list updated
  to include "Slice 14 — 2.13 Long-press notes ✅"; old summary table
  replaced with a fresh table that excludes 2.13 (and several other
  items now closed by slices 7-13).
- `Desktop/migration/slice-reports/slice-14-longpress-confirm.md`
  (this file).

## Audit script re-run

```
$ python3 Desktop/migration/scripts/audit_parity_matrix.py
open-status rows: 9
# Stale-evidence candidates (file present, status not ✅):
# Genuine gaps (file MISSING or no paths):
```

**Zero stale-evidence candidates.** The audit script now returns
only genuinely-open rows.

## Decision: no helper to write

Originally the plan was to extract the inline long-press code into a
shared `useLongPress` hook for reuse. After auditing the actual
implementation:

- The same pattern is used in two places (navigation links at line
  414, tick cells at line 463) but with different timings (150ms vs
  200ms) and different side effects (open context sheet vs emit
  custom event).
- Each variant is <50 lines of plain DOM code — extracting them into
  a hook would add more indirection than it removes.
- The Astro pattern (vanilla `<script>` blocks at the bottom of
  components, mounting via `querySelectorAll`) is the dominant
  pattern across the migrated app — keeping consistency is more
  valuable than DRY.

**No code changes needed for slice 14.**

## Why this slice still matters

The honest result of "next slice candidate" was "another row that
didn't need a slice." That's a useful finding in itself — it
**validates that the matrix triage in slice 13 was thorough** and
that the remaining 9 open rows are genuine gaps (or intentional drops).

## Phase-4 cutover readiness (per apollo-beaverhabits skill)

After slice 14, the only **High-severity** item on the matrix is
**row 1.2 (rpId mismatch)**. The skill `apollo-beaverhabits` confirms:

- Apollo runs `castor:custom-2026-09-14` (legacy Python/NiceGUI).
- The migration branch (`release/astro-migration` on the laptop)
  has not been deployed yet.
- The fork source at `/opt/castor/` on apollo is the **legacy** Python
  code, not the Astro migration. The migration source of truth lives
  on the laptop at `/home/joel/castor-repo`.

So the migration is **source-ready, not deploy-ready**. The next
real-world step is **Phase 4 cutover planning**: build the monorepo
Docker image (per user decision 4), fix `WEBAUTHN_RP_ID` in compose,
bump `token_version`, and roll traffic.

## Files

- `Desktop/migration/parity-matrix.md` (row 2.13 evidence + summary)
- `Desktop/migration/slice-reports/slice-14-longpress-confirm.md`

## Verification

- `python3 Desktop/migration/scripts/audit_parity_matrix.py`:
  0 stale-evidence candidates (was 1 in slice 13).
- No code changes → no tests/astro check needed.
