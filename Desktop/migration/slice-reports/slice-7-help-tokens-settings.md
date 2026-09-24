# Slice 7 — /help + /tokens + /settings + /account/delete — Status Report

**Branch**: `release/astro-migration` (parent `3c58284` from Slice 6.x)
**Date**: 2026-09-24
**Scope**: Add `/help` page (closes the visible 404 from `MoreSheet.tsx`), add `/tokens` page (parity row 12.1), expose `/api/v1/tokens` HTTP routes, and verify `/settings` + `/account/delete` backend contracts are pinned by earlier slices.

---

## What changed

### 1. Backend: `/api/v1/tokens` HTTP routes (`castor/routes/api.py`)
- `GET /api/v1/tokens` → `{"token": "<masked>"}` or `null` (one row per user)
- `POST /api/v1/tokens` → 201 `{"token": "<raw>"}` (only time raw leaks)
- `POST /api/v1/tokens/rotate` → 200 `{"token": "<new-raw>"}`, old invalidated
- `DELETE /api/v1/tokens` → 204, idempotent

### 2. Backend: CRUD bug fix (`castor/app/crud.py`)
- `create_user_api_token` now **upserts** instead of raising IntegrityError on the second call.
- Surface area was the same (one row per user, enforced by unique index on `user_id`); the legacy behaviour was a 500. Slice 7 testing surfaced this because the Astro `/tokens` page POSTs on every "Create token" click.
- Audit event `token_create` still fires on each call.

### 3. New pages (`web/concepts/src/pages/`)
- `help.astro` — closes the mobile MoreSheet dead-end. Static content, no backend, mirrors the `HelpDialog` four links + an FAQ section. No auth required (matches `/terms`, `/privacy`).
- `tokens.astro` — server-rendered, fetches current masked token via `GET /api/v1/tokens` on page load. Form actions POST to backend (`POST`, `POST /tokens/rotate`, `DELETE` with `_method=DELETE` override). Uses `shadcn/ui` `Card` + `CardHeader` + `CardTitle` + `CardDescription` + `CardContent`. Auth-required (redirects to `/login`).

## Evidence (commands + output)

```bash
$ cd /home/joel/castor-repo && .venv/bin/python -m unittest discover -s tests -p test_slice7_tokens.py -v
... 15 tests ...
Ran 15 tests in 9.247s
OK

$ cd /home/joel/castor-repo && .venv/bin/python -m pytest tests/ --ignore=tests/test_batch4_live.py -q
303 passed, 1 warning in 186.91s (0:03:06)
# Was 288 → now 303; delta = +15 from Slice 7

$ cd /home/joel/castor-repo/web/concepts && pnpm exec astro check
Result (94 files):
- 0 errors
- 0 warnings
- 58 hints
# Was 92 → now 94 (help.astro + tokens.astro); +2 file errors fixed mid-slice
```

## What changed (file diff)

| File | Change |
|---|---|
| `castor/routes/api.py` | +47 / −5: imports 4 new CRUD functions; adds 4 `/api/v1/tokens*` routes. |
| `castor/app/crud.py` | +14 / −10: `create_user_api_token` now upserts (delete-then-insert). Audit event unchanged. |
| `tests/test_slice7_tokens.py` | new, +393: 15 tests covering GET/POST/rotate/DELETE/unauth/cross-user isolation. |
| `web/concepts/src/pages/help.astro` | new, +99: closes mobile MoreSheet 404. |
| `web/concepts/src/pages/tokens.astro` | new, +141: server-rendered, GET-only on render, form actions for mutating. |

## Slice 7 findings worth noting

### Bug fix: `create_user_api_token` IntegrityError
**Symptom**: Calling `POST /api/v1/tokens` twice for the same user returned 500 (IntegrityError on the unique `user_id` index in `user_api_tokens`).

**Root cause**: The CRUD function called `session.add()` without checking for an existing row. The legacy NiceGUI app only ever POSTed once and the legacy UI didn't have a "Create token" button that could be clicked twice.

**Fix**: Made `create_user_api_token` idempotent — delete existing then insert, or update the row's `token` column in place. The audit event still fires on each call (one token per user = one create event per session).

**Test**: `tests/test_slice7_tokens.py::Slice7TokensTests::test_create_token_twice_returns_different_tokens` pins the new behaviour.

### `/api/v1/tokens` masking format
The `get_user_api_token` function returns `XXXXXXXX...YYYY` (8 + 4 chars). Tested in `test_get_tokens_returns_masked_form`. The raw token is 43 chars (secrets.token_urlsafe(32)) — the masked form keeps the prefix + suffix for visual identification.

### Cross-user isolation
Tested explicitly: User A's rotate does NOT invalidate User B's token. The `user_id` filter is per-user by construction.

## Out of scope (still)

- **/help search** — static content; would need a docs backend.
- **/tokens UI: copy button + clipboard** — deferred to Slice 9 (browser-side nicety).
- **/settings custom CSS persistence** — parity row 4.2; backend endpoint is TODO. Slice 7 confirms the current "no endpoint" behaviour (404).
- **/account/delete UI flow** — backend pinned by Slice 5; the Astro `AccountContent` React component already has the form. Slice 7 just verifies.
- **Browser E2E** — Playwright, deferred to Slice 9.

## Next proposed slice

**Slice 8 — `/admin` + `/stats`**: 2 medium pages, both have legacy parity requirements. `/admin` requires the operator role check + user listing endpoints. `/stats` requires a date-range aggregation endpoint. Estimated 4–6 hours.

Alternative: **Playwright E2E scaffold** (Slice 9) before more slices — the slice report says "deferred to Slice 9" for too many things.

## Sign-off request

Per the brief, do not move to Slice 8 until you confirm:
1. Slice 7 evidence sufficient (15 tests passing, 2 new pages, 4 new backend endpoints, 1 bug fix)?
2. CRUD bug fix acceptable (was 500 → now upserts; raw token still only returned once)?
3. Slice 8 (admin + stats) — or switch to Playwright?
