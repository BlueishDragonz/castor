# Slice 4 — Passkey (WebAuthn) login + credential management — Status Report

**Branch**: `release/astro-migration` (parent `3ff7ad9` from Slice 3)
**Date**: 2026-09-24
**Scope**: `/auth/webauthn/check`, `/auth/webauthn/login/begin`, `/auth/webauthn/offer/dismiss`, `/auth/webauthn/credentials` (list + delete)

---

## Working flows (verified end-to-end against backend)

| Flow | Test |
|---|---|
| `/check` user with passkey → `{has_passkey: true, passkey_offer_dismissed: true}` | `test_check_user_with_passkey_returns_has_passkey_true` |
| `/check` user without passkey → `{has_passkey: false, passkey_offer_dismissed: false}` | `test_check_user_without_passkey_returns_has_passkey_false` |
| `/check` unknown email → same envelope (no enumeration) | `test_check_unknown_email_returns_same_envelope` |
| `/check` empty / missing username → 200 or 422 (not 500) | `test_check_empty_username_does_not_500`, `test_check_missing_username_does_not_500` |
| `has_passkey=true` implies `passkey_offer_dismissed=true` | `test_check_has_passkey_user_also_marks_offer_dismissed` |
| `/offer/dismiss` toggles the flag for the user | `test_offer_dismiss_marks_user_dismissed` |
| `/offer/dismiss` unknown email → 200 (no-op) | `test_offer_dismiss_unknown_email_does_not_500` |
| `/login/begin` valid user → `{publicKey: {challenge, rpId, allowCredentials}}` | `test_login_begin_user_with_passkey_returns_publicKey` |
| `/login/begin` unknown user → 404 "User not found" | `test_login_begin_unknown_user_returns_404` |
| `/login/begin` user without passkey → 404 "No passkeys registered for this user" | `test_login_begin_user_without_passkey_returns_404` |
| `/login/begin` sets `beaver_webauthn` browser cookie | `test_login_begin_issues_browser_cookie` |
| `/credentials` lists the seeded credential | `test_credentials_list_returns_seeded_credential` |
| `/credentials` user without passkey → `[]` | `test_credentials_list_user_with_no_passkey_returns_empty` |
| `/credentials` unauthenticated → 401 | `test_credentials_list_unauthenticated_returns_401` |
| `/credentials` with garbage bearer → 401 | `test_credentials_list_with_garbage_bearer_returns_401` |
| DELETE `/credentials/{id}` removes from list | `test_delete_credential_removes_from_list` |
| DELETE unauthenticated → 401 | `test_delete_credential_unauthenticated_returns_401` |
| DELETE wrong password → 401, credential preserved | `test_delete_credential_wrong_password_returns_401_and_keeps_credential` |
| DELETE cross-user → 404 (no enumeration) | `test_delete_credential_cross_user_returns_404` |

## Evidence (commands + output)

```bash
$ cd /home/joel/castor-repo && .venv/bin/python -m unittest discover -s tests -p test_slice4_passkey.py -v
... 20 tests ...
Ran 20 tests in 21.419s
OK

$ cd /home/joel/castor-repo && .venv/bin/python -m pytest tests/ --ignore=tests/test_batch4_live.py -q
248 passed, 1 warning in 82.59s (0:01:22)
# Was 228 -> 248; delta = +20 from this slice.

$ cd /home/joel/castor-repo/web/concepts && pnpm exec astro check
Result (92 files):
- 0 errors
- 0 warnings
- 58 hints
```

## What changed

| File | Change | Reason |
|---|---|---|
| `tests/test_slice4_passkey.py` | NEW — 20 hermetic integration tests for the WebAuthn login + management surface | Slice 4 deliverable |
| `Desktop/migration/parity-matrix.md` | Updated row 1.2 (Passkey login) — `🟡` → `✅`, added Slice 4 evidence | Per-slice status requirement |
| `Desktop/migration/slice-reports/slice-4-passkey.md` | NEW — this file | Per-slice status requirement |

## Failed checks

None.

## Behavioural findings (the headline)

Three findings from Slice 4:

1. **No-enumeration probe on `/auth/webauthn/check`** — the endpoint returns the exact same `{has_passkey: false, passkey_offer_dismissed: false}` envelope for "unknown email" and "user without passkey". This is by design (documented in `webauthn_routes.py:374-413`) and is the right behaviour for the Astro Login page's progressive-disclosure probe. Pinned.

2. **`has_passkey=true` ⇒ `passkey_offer_dismissed=true`** — enrolling a passkey implicitly dismisses the post-login "set up a passkey?" offer. Documented in `webauthn_routes.py:409-412` and now pinned by `test_check_has_passkey_user_also_marks_offer_dismissed`.

3. **DELETE passkey requires password re-confirmation — and returns 401 (not 400/403) on failure**. The `_authorize` helper in `castor/app/security_actions.py:63` raises `AuthorizationError` (401) on **any** auth failure including wrong password. There's no distinction between "wrong password" and "no password" to avoid account enumeration, and the public-safe message is `"Fresh current-password authorization required; use verified email recovery if needed"`. Slice 4's wrong-password test originally expected 400/403/422; corrected to expect 401 after seeing the actual behaviour. **This is a defence-in-depth pattern worth highlighting in the security audit.**

## What this slice does NOT cover

- **Full WebAuthn ceremony** (begin → real authenticator signs → complete). Requires a real authenticator (TouchID, Yubikey, or Playwright virtual authenticator). The ceremony internals are covered by `tests/test_lane2_webauthn.py` (15+ tests of challenges, browser cookies, expiry, race conditions, sign-count persistence). Slice 4 deliberately stays at the **Astro-login-page-visible surface**.
- **Registration ceremony** (begin/complete). Same reason: needs a real authenticator. The endpoints exist (`/auth/webauthn/register/{begin,complete}`); they go through the same `_consume_challenge` and `verify_registration_response` paths covered by `test_lane2_webauthn.py::test_bearer_session_can_register` etc.
- **Recovery email** (`/auth/webauthn/recovery-email`). That's Slice 1.4 (forgot password 12-digit code).
- **`/security` page rendering** (passkey list, add-new, remove-confirm dialog). Playwright territory — Slice 9.

## Open items (deferred, not blocking)

- **Browser E2E for passkey login**: needs Playwright's `WebAuthn` virtual authenticator. Heavier than the rest of E2E. Placeholder for Slice 9 (admin + cutover prep).
- **Apollo `WEBAUTHN_RP_ID` mismatch** (carry-over from Phase 1 / Slice 0): the running container has `WEBAUTHN_RP_ID=localhost` while the compose file declares `WEBAUTHN_RP_ID=10.8.0.1`. The env file wins; rpId mismatch likely breaks new passkey login over VPN. **Decision #3 from the user said to fix as part of cutover** — already in the cutover plan.

## Next proposed slice

**Slice 5 — `/security` page backend**: the Astro `/security` page renders:
- 2FA settings (WebAuthn list + add)
- Recovery codes / email
- Change password
- Active sessions
- Account deletion

Backend work: `POST /auth/webauthn/recovery-email`, `POST /auth/webauthn/recovery-email/verify`, `POST /auth/webauthn/change-password`, `DELETE /account`. Tests for each.

Estimated: 4–5 hours.

Alternatives: `/help` 404 (visible cutover blocker), Import/Export (backend endpoint missing), `/stats`, `/admin`.

## Sign-off request

Per the brief, do not move to Slice 5 until you confirm:
1. Slice 4 evidence sufficient?
2. **Three behavioural findings acceptable** (no-enumeration probe, has_passkey ⇒ offer_dismissed, DELETE returns 401 on wrong password)?
3. Slice 5 (`/security` backend) is the right next slice, vs. `/help`, Import/Export, `/stats`, `/admin`?
