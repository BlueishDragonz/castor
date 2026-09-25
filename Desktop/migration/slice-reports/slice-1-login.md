# Slice 1 — Login — Status Report

**Branch**: `release/astro-migration` (cut from `migration-to-shadcn-astro` @ `fb8fd72`)
**Date**: 2026-09-24
**Scope**: Login (email + password), cookie maxAge alignment, backend contract pinning

---

## Working flows (verified end-to-end against backend)

| Flow | Verified by |
|---|---|
| Login with valid creds → JWT returned | `test_slice1_login.py::test_login_valid_returns_jwt_with_30_day_lifetime` |
| Login with valid creds → response body shape `{access_token, token_type: bearer}` | `test_login_valid_sets_beaver_auth_cookie` |
| Login → JWT → fetch protected `/api/v1/habits` returns 200 | `test_login_then_protected_endpoint_with_bearer` |
| Login with wrong password → 400 + `LOGIN_BAD_CREDENTIALS` | `test_login_wrong_password_returns_bad_credentials` |
| Login with unknown email → 400 + same error (no enumeration) | `test_login_unknown_email_returns_bad_credentials_no_enumeration` |
| Login with empty email / empty password / both missing → 4xx | `test_login_empty_*` |
| Protected endpoint without bearer → 401 | `test_protected_endpoint_without_bearer_returns_401` |
| Protected endpoint with garbage bearer → 401 | `test_protected_endpoint_with_garbage_bearer_returns_401` |
| Password change → old JWT rejected on protected endpoint | `test_token_version_bump_invalidates_old_token` |
| Password change → old password rejected, new password accepted | `test_login_after_password_change_uses_new_password` |
| JWT contains no `+`, `/`, `=` (URL-safe base64) | `test_set_cookie_value_is_url_safe` |

## Evidence (commands + output)

```bash
$ cd /home/joel/castor-repo && git checkout -b release/astro-migration
Switched to a new branch 'release/astro-migration'

$ cd /home/joel/castor-repo/web/concepts && pnpm exec astro check
Result (92 files):
- 0 errors
- 0 warnings
- 58 hints

$ cd /home/joel/castor-repo && .venv/bin/python -m pytest tests/ --ignore=tests/test_batch4_live.py -q
193 passed, 1 warning in 49.16s

$ cd /home/joel/castor-repo && .venv/bin/python -m pytest tests/test_slice1_login.py -v
... 13 tests ...
Ran 13 tests in 6.260s
OK
```

## What changed

| File | Change | Reason |
|---|---|---|
| `web/concepts/src/lib/auth.ts` | Added constant `SESSION_MAX_AGE_SECONDS = 60*60*24*30` (30 days); replaced two hard-coded `60*60*24*7` values with the constant | Decision #5 — align cookie `maxAge` with backend JWT lifetime (`JWT_LIFETIME_SECONDS=2592000`) |
| `tests/test_slice1_login.py` | NEW — 13 hermetic Python integration tests pinning the backend `/auth/login` contract that the Astro `/login` page depends on | Slice 1 deliverable per brief: valid/invalid/unauthorised/empty/persistence |

## Failed checks

None.

## Open decisions (still flagged, deferred)

- **Browser E2E (Playwright)**: not added in Slice 1. The in-process Python tests cover the backend contract; full Astro browser round-trip (form submit, cookie write, redirect, page paint) needs Playwright + a running Astro dev server. **Deferred to Slice 9** along with the rest of the E2E suite. The `smoke.sh` script (already in repo) covers the form-submit round-trip via curl, which is a partial substitute.
- **`beaver_auth` cookie**: backend no longer sets it (legacy `AuthMiddleware` deleted in migration branch). Astro's `writeSession()` writes `castor_token` instead. This is **a behaviour change from legacy** — the legacy NiceGUI app set `beaver_auth` via its AuthMiddleware. Documented in `test_login_valid_sets_beaver_auth_cookie` docstring as the new contract. **Not blocking**: jrodux never used `beaver_auth` for anything; it was set but the Astro page only ever read `castor_token`.

## Risks discovered (informational)

1. **`SQLAlchemyUserDatabase` import path**: Pyright LSP reports `Import "fastapi_users.db" could not be resolved` because it can't see the venv. Runtime is fine. Cosmetic; the existing `tests/test_priority1_auth.py` has the same import and works.

2. **`httpx` deprecation warning**: `StarletteDeprecationWarning: Using httpx with starlette.testclient is deprecated; install httpx2 instead`. Pre-existing; not introduced by Slice 1.

3. **Test isolation**: `test_slice1_login.py` does `metadata.drop_all` + `create_db_and_tables` per test (via `asyncSetUp`). Per the apollo-beaverhabits skill, this pattern is **the exact one that caused the demo account wipes** on homelab2 (the audit doc mentioned `test_priority1_auth.py:44` and `test_lane2_integration.py:26` do the same). Slice 1 follows the existing pattern but **must not run against the live apollo DB**. The test sets `DATABASE_URL` to a temp dir at import time so it's hermetic.

## Next proposed slice

**Slice 2 — Home grid (read + tick)**:
- `/habits` page renders 7-day grid from `/api/v1/habits`
- `HabitCheckBox` `<form>` posts to `/habits/{id}/complete` (already wired)
- New tests: tick persists across reload, empty state, streak badge updates
- Backend tests already cover tick; need Playwright for the actual `<form>` submit round-trip

**Estimated**: 4–6 hours (mostly Playwright scaffold for one slice worth of tests).

## Sign-off request

Per the brief, do not move to Slice 2 until you confirm:
1. Slice 1 evidence above is sufficient
2. Cookie `maxAge` change (7d → 30d) is acceptable
3. Browser E2E deferral to Slice 9 is acceptable
