# Slice 23c — Circles invite email (SMTP + HTML template + audit)

**Branch**: `release/astro-migration`
**Date**: 2026-09-25
**Trigger**: dogfood finding H (`/api/v1/circles/{id}/invites` with
`delivery='email'` previously did NOT call `send_email` — the invite
row was created but the recipient was never notified).
**Builds on**: `45e7548` (slice-23a self-join guard + raw_token)
**Commit**: `<this commit>`

## What this slice added

### Backend — `castor/app/circle_routes.py::create_invite`

After `record("circle_invite_create")`, when `body.delivery == "email"`:

1. Build the join URL from `settings.FRONTEND_URL` + `/circles/{id}/join?token=…`
   (URL-quoted).
2. Render `castor/templates/circle_invite.{html,txt}` via the new
   `castor.app.circle_emails.render_invite_email()` helper.
3. Call `castor.utils.send_email(subject, body, recipients, html_body)`
   with the rendered parts.
4. On success: `record("circle_invite_sent", outcome="success")` +
   `logger.info(...)`.
5. On exception: `record("circle_invite_sent", outcome="failure")` +
   `logger.warning(...)`. **The invite row is NOT rolled back** — the
   user keeps the `raw_token` from the response and can re-share via
   copy-link.

We do the email send AFTER `record("circle_invite_create")` and AFTER
returning the row, so an SMTP outage doesn't undo a perfectly good mint.

### Configuration — `castor/configs.py`

Four new fields in `Settings`:

- `SMTP_HOST: str = ""` — empty = legacy `smtp.gmail.com:465` SSL
  fallback (preserves the slice-22-era single-user Gmail setup);
  non-empty triggers the env-driven path.
- `SMTP_PORT: int = 587` — STARTTLS default.
- `SMTP_FROM: str = ""` — empty = fall back to `SMTP_EMAIL_USERNAME`.
- `SMTP_USE_TLS: bool = True`.

Plus `FRONTEND_URL: str = "http://localhost:4321"` for the join URL.

### Email helper — `castor/utils.py::send_email`

Preserves the legacy Gmail path AND adds the env-driven path:

- `SMTP_HOST` empty → legacy `smtp.gmail.com:465` SSL (unchanged).
- `SMTP_HOST` set + `SMTP_USE_TLS=True` → STARTTLS on `SMTP_PORT`.
- `SMTP_HOST` set + `SMTP_USE_TLS=False` → plaintext relay.
- `SMTP_DEV_LOCAL_OUTBOX` local-outbox path unchanged (used by the
  integration test below).

### Audit event — `castor/app/audit.py::EVENTS`

Added `"circle_invite_sent"` (with the standard `success` /
`failure` / `denied` outcomes). Distinct from the existing
`circle_invite_create` so we can answer "did the email go out?" without
joining to SMTP logs.

### Templates — `castor/templates/circle_invite.{html,txt}`

Privacy-first, neutral brand. Rendered with `string.Template` so the
CSS braces don't need doubling. Variables:

- `inviter_email`, `circle_name`, `join_url`, `expires_hours`,
  `frontend_origin`.

The HTML part has: wordmark, eyebrow ("PRIVATE CIRCLE INVITE"), h1
headline, body copy explaining privacy defaults, button-styled "Accept
invite" CTA, fallback plain-text link, expiry + privacy note, and a
`<details>` plaintext version for clients that strip multipart.

### Docker — `docker-compose.yml`

Seven new env vars on the `castor` service:

```
SMTP_HOST, SMTP_PORT, SMTP_USE_TLS,
SMTP_EMAIL_USERNAME, SMTP_EMAIL_PASSWORD, SMTP_FROM,
FRONTEND_URL
```

Each uses `${VAR:-default}` so the file remains usable in dev without
real credentials. Real production values come from
`./.user/.secrets.env` on apollo per the existing pattern.

### Import shape change — `castor/app/circle_routes.py`

`from castor.utils import send_email` is now at module top (was lazy
inside `create_invite`). This is required for the test harness
(`unittest.mock.patch.object(circle_routes, 'send_email', …)`); the
lazy import path also worked but couldn't be intercepted by
`patch.object` from the outside.

## Tests — `tests/test_slice23c_circles_email.py` (9 pass)

```
Slice23cEmailDeliveryTests
  test_email_delivery_calls_send_email_with_html_body
  test_email_body_includes_join_url
  test_email_subject_includes_inviter_and_circle_name
  test_link_delivery_does_not_call_send_email   ← belt-and-braces
  test_email_send_failure_does_not_5xx          ← SMTP outage ≠ 5xx
Slice23cAuditEventTests
  test_audit_event_circle_invite_sent_recorded
  test_audit_event_failure_outcome
Slice23cSettingsTests
  test_smtp_settings_are_loaded
Slice23cLocalOutboxIntegrationTests
  test_email_drops_file_to_outbox               ← reads the .eml + decodes MIME parts
```

### Notable test patterns pinned

- The route is patched via `patch.object(circle_routes_module, 'send_email')`
  — `send_email` MUST be importable at module top for this. (Why this
  slice also moved the import up.)
- `test_email_send_failure_does_not_5xx` raises `RuntimeError('SMTP down')`
  from inside the mock; the route must still return 201 with
  `raw_token` in the response so the user can re-share.
- `test_email_drops_file_to_outbox` uses
  `SMTP_DEV_LOCAL_OUTBOX=/tmp/…`, then `email.message_from_string()` +
  `walk()` to assert on the rendered HTML and plaintext parts (not the
  base64-encoded wire bytes) — catches template regression without
  being too brittle.

## Notable pitfalls fixed during this slice

1. `EVENTS` frozenset did not include `circle_invite_sent` → audit
   writes were silently swallowed by `record()` (the `try/except` in
   audit.py logs "Security audit write failed" but raises `ValueError`).
2. `OUTCOMES` is `{"success","failure","denied"}`, not `{"success",
   "failure","failed"}` — used the wrong token first.
3. Lazy `from castor.utils import send_email as _send_email` inside
   `create_invite` makes the function immune to
   `patch.object(circle_routes, 'send_email')` — moved to module top.
4. `circle_invite_route` discarded the circle returned by
   `_require_owner`; we now re-fetch via `_load_circle` so the email
   subject carries the circle name.

## What's still missing toward the standing goal

- **Browser E2E**: tests are server-side only. The full
  owner-creates-circle → owner-mints-email-invite → alice-registers →
  alice-clicks-invite-link → welcome page should still be walked
  manually via the dev browser (one-shot dogfood pass; the prior
  session's slice-23b subagent started this and was interrupted at
  the alice-side).
- **Apollon SMTP wiring**: this slice added the settings + compose
  entries; actual credentials on apollo / `/opt/castor/.user/.secrets.env`
  still need the SMTP provider chosen (Mailgun / SES / SMTP relay) —
  operational decision, not code.
- **Cutover image pin**: `docker-compose.yml` still has
  `castor:custom-2026-09-XX` placeholder.
- **DB-wipe mystery alive**: demo user keeps disappearing between
  dev sessions; only `sort@example.com` survives.
