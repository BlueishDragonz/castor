# Slice 5 — /security page backend — Status Report

**Branch**: `release/astro-migration` (parent `f52c80e` from Slice 4)
**Date**: 2026-09-24
**Scope**: `/auth/webauthn/verify-password`, `/auth/webauthn/change-password`, `/auth/webauthn/recovery-email` + `/recovery-email/verify`, `DELETE /api/v1/account`

---

## Working flows (verified end-to-end against backend)

| Flow | Test |
|---|---|
| verify-password happy path → `{verified: true}` | `test_verify_password_with_correct_password_returns_verified_true` |
| verify-password wrong → 401 "Current password is incorrect" | `test_verify_password_with_wrong_password_returns_401` |
| verify-password unauthenticated → 401 | `test_verify_password_unauthenticated_returns_401` |
| verify-password empty → 401 or 422 (uniform failure mode) | `test_verify_password_empty_password_returns_401` |
| change-password happy path + token_version bump + invalidates old JWT | `test_change_password_succeeds_and_bumps_token_version` |
| change-password wrong current → 401 "Fresh current-password..." | `test_change_password_wrong_current_password_returns_401` |
| change-password unauthenticated → 401 | `test_change_password_unauthenticated_returns_401` |
| recovery-email request → `{pending, email, expires_at}` + send_email call | `test_recovery_email_request_returns_pending_envelope` + `test_recovery_email_sends_email` |
| recovery-email invalid format → 422 | `test_recovery_email_invalid_format_returns_422` |
| recovery-email verify with extracted code → `{recovery_email, recovery_email_verified: true}` | `test_recovery_email_verify_with_correct_code_succeeds` |
| recovery-email verify wrong code → 4xx (not 200) | `test_recovery_email_verify_with_wrong_code_returns_400` |
| recovery-email verify non-digit code → 400 "Code must be exactly 6 digits" | `test_recovery_email_verify_invalid_code_format_returns_400` |
| recovery-email verify 5-digit code → 400 | `test_recovery_email_verify_code_too_short_returns_400` |
| recovery-email remove=True (no email set) → idempotent 200 | `test_recovery_email_verify_remove_is_idempotent_when_no_email` |
| DELETE /api/v1/account → 204 + JWT invalidation | `test_delete_account_returns_204_and_clears_habit_list` |
| DELETE unauthenticated → 401 | `test_delete_account_unauthenticated_returns_401` |
| DELETE garbage bearer → 401 | `test_delete_account_with_garbage_bearer_returns_401` |
| DELETE then re-register same email → 201 or 400 (not 500) | `test_delete_account_blocks_relogin_with_same_email` |

## Evidence (commands + output)

```bash
$ cd /home/joel/castor-repo && .venv/bin/python -m unittest discover -s tests -p test_slice5_security.py -v
... 21 tests ...
Ran 21 tests in 11.079s
OK

$ cd /home/joel/castor-repo && .venv/bin/python -m pytest tests/ --ignore=tests/test_batch4_live.py -q
269 passed, 1 warning in 184.75s (0:03:04)
# Was 248 -> 269; delta = +21 from this slice.

$ cd /home/joel/castor-repo/web/concepts && pnpm exec astro check
Result (92 files):
- 0 errors
- 0 warnings
- 58 hints
```

## What changed

| File | Change | Reason |
|---|---|---|
| `tests/test_slice5_security.py` | NEW — 21 hermetic integration tests for /security backend | Slice 5 deliverable |
| `Desktop/migration/parity-matrix.md` | Updated rows 5.4, 5.5 with Slice 5 evidence; added new row 5.7 (delete account) | Per-slice status requirement |
| `Desktop/migration/slice-reports/slice-5-security.md` | NEW — this file | Per-slice status requirement |

## Failed checks

None.

## Findings (the headline)

### 1. Two audit-event claims in the parity matrix were wrong

While writing Slice 5 tests I had to verify the actual backend code for the audit events claimed in rows 5.4 and 5.5:

- **Row 5.4 (change password) claimed** `audit.append_audit_event("password_change", ...)`. **Wrong**: `castor/app/security_actions.py:117` (`change_password`) does NOT emit this audit event.
- **Row 5.5 (recovery email set) claimed** `audit.append_audit_event("recovery_email_set", ...)`. **Wrong**: `castor/app/webauthn_routes.py::set_recovery_email` does not emit audit on the set path. The verify-remove path DOES emit `audit.record("recovery_email_removed", ...)` (line 862).

**Action taken**: removed the incorrect audit claims from both rows, flagged for **Phase 4 audit-reconciliation step**. The audit emission on the migration branch is significantly thinner than the parity matrix claimed. This is exactly the kind of finding the brief's "do not invent features" rule is designed to surface.

### 2. `verify-password` is the standard step-up pattern

The endpoint returns `{verified: true}` on success and `401 "Current password is incorrect"` on failure. **No 400 or 403** — uniform failure mode for enumeration safety. The `/security` page's step-up dialog calls this before any sensitive action (passkey delete, password change).

### 3. `change-password` is the only endpoint that bumps `token_version`

Slice 1 tests already verified that `token_version` bumps invalidate existing JWTs. Slice 5 confirms that `change-password` triggers this same flow. **The Astro `/security` page must warn the user that all sessions will be signed out** after a password change — that's the same UX as the legacy NiceGUI app.

### 4. Recovery email uses a single-shot 6-digit code with TTL

The challenge row is tombstoned (used=True) as soon as a new request comes in, so a stale code cannot be replayed. The 5-minute rate-limit (`recovery_request_user:{user_id}`) is bypassed in tests via `AUTH_RATE_USER_PER_MINUTE=10000`. **Real deployments will throttle this**; the test fixture deliberately disables it.

### 5. `DELETE /api/v1/account` returns 204 with no body

The Astro `/account/delete` page must not try to parse a response body. Status-only contract.

## Open items (deferred, not blocking)

- **Browser E2E for /security** — needs Playwright. Deferred to Slice 9 (admin + cutover prep).
- **Audit-event reconciliation** — the brief's "Phase 4 release gates" should include a step to reconcile the audit-event claim with the actual backend (which audit events are emitted where). Flagged in the matrix updates.
- **Recovery email HTML rendering test** — the `html_body` of the recovery email is rich HTML. Slice 5 only checks subject + body text + recipients. Visual rendering of the email template is Playwright territory (use a fake SMTP server + screenshot).

## Next proposed slice

**Slice 6 — Import / Export**:
- `GET /api/v1/habits/export` (JSON download) — already pinned in `test_apis.py`
- `GET /api/v1/habits/export` with `?format=csv`
- `POST /api/v1/habits/import` (multipart upload)
- New tests: round-trip import → export → compare, malformed JSON, oversized file

Estimated: 4–5 hours. **This is also a parity-matrix gap flagged in Phase 1** — the import endpoint reads but does not write per the audit doc.

Alternatives: `/help` 404 (visible cutover blocker), `/stats`, `/admin`, `/tokens`.

## Sign-off request

Per the brief, do not move to Slice 6 until you confirm:
1. Slice 5 evidence sufficient?
2. **Two audit-event claims removed from the parity matrix** — acceptable?
3. Slice 6 (Import/Export) is the right next slice, vs. `/help`, `/stats`, `/admin`, `/tokens`?
