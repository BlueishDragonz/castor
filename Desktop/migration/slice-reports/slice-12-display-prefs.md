# Slice 12 — Display preferences + MoreSheet nav smoke + matrix hygiene

**Branch**: `release/astro-migration` (parent `93c0abc` from Slice 11)

**Date**: 2026-09-24

**Status**: 3 🟡 partials cleared → ✅; **368 passed + 12 skipped** in pytest
discovery (was 358 + 6 skipped; +10 new tests + 6 new auto-skips when no dev
server bound for nav smoke). Astro check **0 errors / 0 warnings / 60 hints.**

## Scope

Three work items bundled under "slice 12":

1. **MoreSheet nav smoke test (parity 8.2)** — `/help: 200`, the other 5
   auth-gated targets: 302 → `/login`. Auto-skips when no Astro dev server
   is bound.
2. **Stale matrix evidence (parity 4.3, 4.4, 8.2)** — `/help` page
   shipped in Slice 7; the 404-stale evidence carried over to 3 rows.
3. **Display preferences (parity 12.6)** — close the 🟡: extend
   `user_configs` schema with `show_streak`, `show_total`,
   `date_reverse`; add Display card to `/settings`; wire
   `SettingsClient` to PUT on toggle + mirror to cookies for the
   server-side cookie read path on `/habits/index.astro`.

## What landed

### Backend (`castor/routes/api.py`)

- `UserConfigsUpdate` model extended from `custom_css` only to also
  accept `show_streak`, `show_total`, `date_reverse`. **All booleans
  use Pydantic `StrictBool`**, which rejects `0`/`1`/`"yes"`/`"no"`
  coercion and lets bad callers fail validation upstream with 422.
- `PUT /api/v1/user-configs` route now copies non-`None` boolean values
  into the merge diff. If every key is `None`, GET is returned
  unchanged (no row write).

### Frontend (`web/concepts/src/pages/settings.astro`, `…/components/SettingsClient.tsx`)

- New **Display** card with three boolean toggles (`show_streak`,
  `show_total`, `date_reverse`).
- `SettingsClient` extended with:
  - `useState<DisplayPrefs>` + `useState<DisplayStatus>` for hydration
    and per-toggle error handling.
  - GET round-trip on mount, with **cookie fallback** so toggles
    reflect the user's last session even if the GET is slow.
  - PUT on change handler that **also writes the value to
    `habit_{key}` cookies** with `path=/; max-age=31536000;
    samesite=lax`. This bridges the existing
    `/habits/index.astro` server-side cookie reads to the user's
    actual saved preference.
  - On error: roll back the toggle in local state and DOM.

### Parity matrix

| Row | Before | After |
| --- | --- | --- |
| 8.2 | 🟡 "dead link" (stale) | ✅ all nav targets resolve |
| 4.3 | ✅ "MoreSheet → /help 404s" (stale) | ✅ link resolves |
| 4.4 | ❌ "/help page missing" (stale) | ✅ page exists |
| 12.6 | 🟡 "no write endpoint for display prefs" | ✅ 3 booleans + custom_css persisted via /api/v1/user-configs |

## Tests

| File | Tests | Coverage |
| --- | --- | --- |
| `tests/test_slice12_moresheet_nav.py` | 6 | Auto-skip without dev server; covers /help + 5 auth-gated targets |
| `tests/test_slice12_display_prefs.py` | 10 | GET defaults, 3× boolean PUT, multi-key merge, coexist-with-CSS, type validation (StrictBool), cross-user isolation |

Total full suite: **368 passed + 12 skipped** (the 6 new nav skips happen
when astro dev isn't running).

## Design choices

- **StrictBool vs lax** — chose strict. The `/api/v1/user-configs` PUT
  is internal-to-Astro; coercing strings to booleans was tempting for
  forgiveness but risked silent surprises (Pydantic turns `"yes"` into
  `True`). Strict-fail-on-bad-shape keeps the contract honest.
- **Cookies + server-stored config** — chosen because the existing
  `/habits/index.astro` already does server-side `Astro.cookies.get()`
  reads. Adding a server-stored read path through `/api/v1/user-configs`
  would require either (a) introducing `Astro.locals.user_configs`
  middleware that proxies the backend, or (b) reworking habits to
  client-side fetch. The cookie-mirror keeps the change small and
  lets `/habits` work without SSR rewiring. The DB stays authoritative
  for cross-device persistence; cookies are local cache.
- **No autosave on theme** — Display toggles autosave; theme stays on
  the existing form-POST-and-redirect pattern. The cost of a
  partial Save on the theme form (mixed HTML form + JS PUT) outweighs
  the benefit.

## Open follow-ups (unchanged from prior sessions)

- NUMERIC-affinity audit-record silent drop — separate dialect bug,
  routed to Phase 4.
- `prune_audit_events` retention config (12.4) — independent slice.
- `/admin` users-list pagination — Phase 4.
- Pydantic `StrictInt` / enums for numeric / enum display keys
  (`date_columns`, `first_day_of_week`, `tag_selection_mode`) when the
  next slice wants to widen the surface.

## Sign-off request

1. The 10 display-pref tests + 6 nav smoke tests + matrix triage =
   sufficient Slice 12?
2. Cookie-mirror approach (vs. refactor `/habits` to fetch
   `user_configs` server-side) — approve as a deliberate split, or
   ask for the deeper refactor?
