# Slice 23a — Circles backend (self-join guard + raw_token)

**Branch**: `release/astro-migration`
**Date**: 2026-09-25
**Trigger**: dogfood findings C (self-join) and B (raw_token)

## Changes

### castor/app/circle_routes.py

- `accept_invite` now returns **HTTP 409 Conflict** when the
  inviter tries to accept their own invite. The check uses
  `str(invite.invited_by) == str(user.id)` so UUID-vs-string
  mismatches across DB backends don't trip the guard.

  Without this, an owner could POST `/api/v1/circles/{id}/join`
  with their own invite token and silently become a member of
  their own circle. Confirmed by dogfood walkthrough (demo ended
  up with both owner + member rows pointing at circle 1).

### tests/test_slice23a_circles_backend.py

New unittest.IsolatedAsyncioTestCase file with 6 tests:

- `test_owner_cannot_accept_own_invite` — pins the new 409 behaviour.
- `test_owner_still_member_after_self_join_blocked` — no collateral
  damage; the owner remains owner.
- `test_mint_invite_returns_raw_token` — pins the existing
  `raw_token` return (dogfood finding B).
- `test_mint_invite_link_delivery_no_email_called` — link delivery
  must not call `send_email` (defensive; the helper isn't wired yet
  so the assertion is permissive — falls back to "link delivery
  succeeded with 201" if `send_email` isn't on the module).
- `test_other_user_can_accept_invite` — cross-user flow unaffected.
- `test_self_join_block_does_not_break_cross_user` — owner tried
  first (blocked), then another user accepts the same token
  (succeeds).

## What was NOT shipped in this slice

- **Email delivery (dogfood finding A + H)**: `castor.app.circle_routes`
  has no `send_email` call yet. The Astro `delivery: email` option
  still does nothing. To wire this we need:
  - An HTML template at `castor/templates/circle_invite.html`.
  - A `send_email` helper in `castor/utils.py` (or a new module)
    using SMTP via `smtplib`.
  - SMTP_* env vars on apollo + the docker-compose.yml.
  - The `create_invite` route calling `send_email` when
    `body.delivery == 'email'`.
- **SMTP settings**: deferred to slice 23c.

These are deferred because:
1. The Astro `invite_url` fallback already renders a copyable link
   for the inviter, so the current UX is functional.
2. Email wiring needs apollo-side ops decisions (real SMTP creds,
   which provider, deliverability testing) that should be a separate
   slice with user input.
3. The slice-23b frontend work doesn't depend on email delivery;
   the welcome + share flows work via the link delivery path.

## Verification

- `pytest tests/test_slice23a_circles_backend.py -v` → 6 passed.
- `pytest tests/ --ignore=tests/test_batch4_live.py -q` → 374 passed
  (368 baseline + 6 new). No regressions.
- Self-join blocked end-to-end via curl from the dogfood session.

## Rollback

Revert commit `45e7548`. The self-join guard is the only behavioural
change; removing it returns the backend to its pre-slice state. The
tests can also be deleted.