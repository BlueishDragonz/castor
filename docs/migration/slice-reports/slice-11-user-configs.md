# Slice 11 — /api/v1/user-configs persistence (parity row 4.2 — Custom CSS)

**Branch**: `release/astro-migration` (parent `eb28b2d` from the
tz-normalisation extension of Slice 10-alt)

**Date**: 2026-09-24

**Status**: 13 contract tests pass; full suite **345 → 358 passed** (+13);
Astro check `0 errors / 0 warnings / 59 hints`.

## Scope

Close the 🟡 on parity row **4.2 Custom CSS** by giving the
`/settings` page a real backend write path. Before this slice, the
custom CSS textarea on `/settings` hydrated from `localStorage` only —
switching browser, clearing site data, or signing in on another
device lost it. The Astro UI shipped a "Save" affordance that
silently never persisted.

## What landed

### Backend (`castor/routes/api.py`)

- **`GET /api/v1/user-configs`** → returns the user's persisted config
  dict, or `{}` if none yet.
- **`PUT /api/v1/user-configs`** → accepts `{custom_css?: str}`,
  sanitises via the existing `castor/css_sanitizer.sanitize_css()`,
  merges into `user_configs.config_data` through the existing CRUD
  helper `update_user_configs()`. Returns the merged dict.
  Empty string is allowed (clears stored CSS). Disallowed CSS returns
  **422** with a friendly detail and does **not** mutate the row.
- The model is reused (`UserConfigsModel` from Slice 2 — already
  existed in `castor/app/db.py`); only the HTTP surface was missing.

### Frontend (`web/concepts/src/components/SettingsClient.tsx`, `web/concepts/src/pages/settings.astro`)

- `SettingsClient` now performs:
  1. On mount, GET `/api/v1/user-configs` and populate the textarea
     (with a localStorage cache hint so it never appears empty).
  2. On Save-button click, PUT `/api/v1/user-configs` with the textarea
     value.
  3. On 200, write the same value back to localStorage so the next
     visit doesn't have to re-GET to render.
  4. Surface a status banner: "Saving…" / "Saved on server at HH:MM:SS"
     / "Save rejected (HTTP 422): …". No silent drop.
- `settings.astro` was patched to render a `<Button id="custom-css-save">Save CSS</Button>`
  next to the textarea, and the lingering "this isn't shipped yet"
  copy in the help text was rewritten to reflect the new behaviour.

### Parity matrix (`Desktop/migration/parity-matrix.md` row 4.2)

`Status: 🟡 → ✅` with evidence pointing at the tests + endpoints.

## Evidence

| Check | Before | After |
| --- | --- | --- |
| `tests/test_slice11_user_configs.py` | (didn't exist) | **13 ✓** in 8.4s |
| Full suite (S1–S10-alt + slice 11) | 345 ✓ + 6 skip | **358 ✓ + 6 skip** (+13) |
| `pnpm exec astro check` (94 files) | 0 errors / 0 warnings / 59 hints | **0 / 0 / 59** (no regression) |

## What the slice does **not** fix

- **Habit-display preference toggles** (streak badge, total badge, date
  columns, tag filters, calendar — parity row 12.6). The `PUT` body
  schema deliberately only accepts `custom_css` for now; new keys would
  require both backend whitelisting and an Astro UI surface area, and
  are tracked under the same 12.6 row in the matrix. Still 🟡.
- **NUMERIC-affinity audit-record silent drop** — separate dialect bug
  from the tz-comparison fixes in the previous commit (`eb28b2d`).
  Still routed to Phase 4.
- **`prune_audit_events()` retention config** (parity row 12.4) — still
  unimplemented. Owner: independent slice.

## Sign-off request

1. The 13 tests cover GET defaults, PUT happy path, PUT empty clears,
   sanitiser rejection (var()/expression/@import all 422 without
   writing), merge semantics, cross-user isolation, and 401 gates.
   Acceptable coverage for the parity row?
2. The TODO comment in `SettingsClient`'s old "What this client
   deliberately does NOT do" section has been updated to reflect that
   `custom_css` is now wired. The habit-display toggles text remains,
   referencing the user_configs backend extension as the gating work.
3. The 4.2 row in `parity-matrix.md` now ✅. Drop the row from the open
   TODO list (slice 11 deferred / partials) accordingly.
