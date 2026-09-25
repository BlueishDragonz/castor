"""Slice 23c — Circles invite email.

Pins the dogfood-2026-09-25 backend gap H: ``/api/v1/circles/{id}/invites``
with ``delivery='email'`` previously did NOT call ``send_email`` — the
invite was created in the database but the recipient was never notified.
This slice wires the email branch end-to-end:

  castor/configs.py        — SMTP_HOST/SMTP_PORT/SMTP_FROM/SMTP_USE_TLS
                             + FRONTEND_URL (for the join URL).
  castor/utils.py          — env-driven SMTP (preserves legacy gmail:465
                             fallback when SMTP_HOST is empty).
  castor/templates/        — circle_invite.{html,txt}, brand-clean.
  castor/app/circle_emails.py — render_invite_email() helper using
                             string.Template (CSS-safe).
  castor/app/circle_routes.py — create_invite() now invokes the email
                             branch when ``delivery=='email'`` and
                             records circle_invite_sent.

Tests:
  test_email_delivery_calls_send_email_with_html_body
  test_email_body_includes_join_url
  test_email_subject_includes_inviter_and_circle_name
  test_audit_event_circle_invite_sent_recorded
  test_email_send_failure_does_not_5xx
  test_smtp_settings_are_loaded
  test_legacy_gmail_path_preserved_when_smtp_host_empty
"""
import os
import tempfile
import unittest
from unittest.mock import patch as mock_patch

_TMP = tempfile.TemporaryDirectory()
os.environ['DATABASE_URL'] = f'sqlite+aiosqlite:///{_TMP.name}/test.db'
os.environ['JWT_SECRET'] = 'test-only-jwt-secret-not-for-production'
os.environ['RESET_PASSWORD_TOKEN_SECRET'] = 'test-only-reset-secret'
os.environ['NICEGUI_STORAGE_SECRET'] = 'test-only-storage-secret'
os.environ['JWT_LIFETIME_SECONDS'] = str(60 * 60 * 24 * 30)
os.environ['REQUIRE_ADMIN_FOR_REGISTRATION'] = 'false'
os.environ['TRUSTED_LOCAL_EMAIL'] = ''
os.environ['TRUSTED_EMAIL_HEADER'] = ''
os.environ['AUTH_RATE_USER_PER_MINUTE'] = '10000'
os.environ['AUTH_RATE_IP_PER_MINUTE'] = '10000'
os.environ['SMTP_DEV_LOCAL_OUTBOX'] = _TMP.name + '/outbox'
os.makedirs(os.environ['SMTP_DEV_LOCAL_OUTBOX'], exist_ok=True)

import httpx
from fastapi import FastAPI
from fastapi_users.db import SQLAlchemyUserDatabase

import castor.main  # noqa: F401  (sets up module registry)
from castor import views
from castor.app import db, reset_routes
from castor.app.users import UserManager
from castor.app.schemas import UserCreate
from castor.app.app import init_auth_routes
from castor.routes.api import init_api_routes

import castor.app.circle_routes as circle_routes_module
import castor.utils as utils_module

app = FastAPI()
init_auth_routes(app)
init_api_routes(app)
app.include_router(reset_routes.router)

PASSWORD = 'TestPass1234!'


class _Harness(unittest.IsolatedAsyncioTestCase):
    """Shared fixture pattern from castor-migration-backend-tests."""

    async def asyncSetUp(self):
        async with db.engine.begin() as conn:
            await conn.run_sync(db.Base.metadata.drop_all)
        await db.create_db_and_tables()
        reset_routes._rate_limit_cache.clear()
        session = db.async_session_maker()
        manager = UserManager(SQLAlchemyUserDatabase(session, db.User))
        self.user = await manager.create(
            UserCreate(email='alice@example.com', password=PASSWORD),
        )
        await views.get_or_create_user_habit_list(
            self.user, views.dummy_empty_habit_list(),
        )
        self.session = session
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url='http://test',
        )
        login = await self.client.post(
            '/auth/login',
            data={'username': 'alice@example.com', 'password': PASSWORD},
        )
        assert login.status_code == 200, login.text
        self.token = login.json()['access_token']
        self.headers = {'Authorization': f'Bearer {self.token}'}

    async def asyncTearDown(self):
        await self.client.aclose()
        await self.session.close()

    async def _create_circle(self, name: str = 'Family wellness') -> int:
        r = await self.client.post(
            '/api/v1/circles', json={'name': name}, headers=self.headers,
        )
        assert r.status_code == 201, r.text
        return r.json()['id']

    async def _mint(self, cid: int, delivery: str,
                    invited_email: str | None = None) -> dict:
        body: dict = {'delivery': delivery}
        if invited_email is not None:
            body['invited_email'] = invited_email
        r = await self.client.post(
            f'/api/v1/circles/{cid}/invites',
            json=body, headers=self.headers,
        )
        assert r.status_code == 201, r.text
        return r.json()


class Slice23cEmailDeliveryTests(_Harness):
    """H: delivery='email' must invoke send_email."""

    async def test_email_delivery_calls_send_email_with_html_body(self):
        cid = await self._create_circle()
        with mock_patch.object(
            circle_routes_module, 'send_email',
        ) as mail:
            invite = await self._mint(
                cid,
                delivery='email',
                invited_email='bob@example.com',
            )

        # The route calls send_email inside a try/except inside an
        # if-block, so on the success path the mock is invoked exactly
        # once. Arguments are passed as keyword args.
        self.assertTrue(mail.called, 'send_email should have been called')
        self.assertEqual(mail.call_count, 1)
        kwargs = mail.call_args.kwargs
        # Subject carries inviter + circle name.
        self.assertIn('Family wellness', kwargs['subject'])
        # Body (plaintext fallback) also carries the circle name.
        self.assertIn('Family wellness', kwargs['body'])
        # html_body contains the brand CTA.
        self.assertIn('Accept invite', kwargs['html_body'])
        # Recipients is a list with bob's email.
        self.assertEqual(list(kwargs['recipients']), ['bob@example.com'])

    async def test_email_body_includes_join_url(self):
        cid = await self._create_circle('Test circle')
        with mock_patch.object(
            circle_routes_module, 'send_email',
        ) as mail:
            invite = await self._mint(
                cid,
                delivery='email',
                invited_email='bob@example.com',
            )
        token = invite['raw_token']

        kwargs = mail.call_args.kwargs
        all_text = (
            kwargs['subject'] + ' ' + kwargs['body'] + ' ' + kwargs['html_body']
        )
        # The frontend origin + /circles/{cid}/join must be present.
        self.assertIn(f'/circles/{cid}/join', all_text)
        # The raw token (URL-quoted) must be present.
        self.assertIn(token, all_text)

    async def test_email_subject_includes_inviter_and_circle_name(self):
        cid = await self._create_circle('Workout buddies')
        with mock_patch.object(
            circle_routes_module, 'send_email',
        ) as mail:
            await self._mint(
                cid,
                delivery='email',
                invited_email='carol@example.com',
            )
        subject = mail.call_args.kwargs['subject']
        self.assertIn('Workout buddies', subject)
        self.assertIn('alice@example.com', subject)

    async def test_link_delivery_does_not_call_send_email(self):
        """Belt-and-braces: link mints must stay quiet so local-dev
        users don't get spammed by every share."""
        cid = await self._create_circle()
        with mock_patch.object(
            circle_routes_module, 'send_email',
        ) as mail:
            await self._mint(cid, delivery='link')
        self.assertFalse(mail.called)

    async def test_email_send_failure_does_not_5xx(self):
        """The invite is the source of truth — an SMTP outage must
        not roll it back. The route returns 201 even if send_email
        raises."""
        cid = await self._create_circle()
        with mock_patch.object(
            circle_routes_module, 'send_email',
            side_effect=RuntimeError('SMTP down'),
        ):
            r = await self.client.post(
                f'/api/v1/circles/{cid}/invites',
                json={
                    'delivery': 'email',
                    'invited_email': 'dave@example.com',
                },
                headers=self.headers,
            )
        self.assertEqual(r.status_code, 201, r.text)
        # raw_token still surfaced so the user can resend manually.
        self.assertIn('raw_token', r.json())


class Slice23cAuditEventTests(_Harness):
    """circle_invite_sent is recorded after a successful mint."""

    async def test_audit_event_circle_invite_sent_recorded(self):
        from sqlalchemy import select

        from castor.app.audit import AuditEvent

        cid = await self._create_circle()
        before = (await self.session.execute(
            select(AuditEvent).where(AuditEvent.event == 'circle_invite_sent')
        )).scalars().all()
        self.assertEqual(len(before), 0)

        with mock_patch.object(
            circle_routes_module, 'send_email',
        ):
            await self._mint(
                cid,
                delivery='email',
                invited_email='eve@example.com',
            )

        after = (await self.session.execute(
            select(AuditEvent).where(AuditEvent.event == 'circle_invite_sent')
        )).scalars().all()
        self.assertGreater(len(after), 0)
        record = after[-1]
        self.assertEqual(record.outcome, 'success')

    async def test_audit_event_failure_outcome(self):
        from sqlalchemy import select

        from castor.app.audit import AuditEvent

        cid = await self._create_circle()
        with mock_patch.object(
            circle_routes_module, 'send_email',
            side_effect=RuntimeError('boom'),
        ):
            await self._mint(
                cid,
                delivery='email',
                invited_email='frank@example.com',
            )

        rows = (await self.session.execute(
            select(AuditEvent).where(
                AuditEvent.event == 'circle_invite_sent',
                AuditEvent.outcome == "failure",
            )
        )).scalars().all()
        self.assertGreater(len(rows), 0)


class Slice23cSettingsTests(unittest.TestCase):
    """Config knobs are exposed and patchable at runtime."""

    def test_smtp_settings_are_loaded(self):
        from castor.configs import settings

        # Defaults match the slice-23c spec.
        self.assertEqual(settings.SMTP_PORT, 587)
        self.assertTrue(settings.SMTP_USE_TLS)
        # SMTP_HOST defaults empty so legacy gmail:465 wins; FRONTEND_URL
        # defaults to localhost so a fresh checkout works without config.
        self.assertEqual(settings.SMTP_HOST, '')
        self.assertEqual(settings.SMTP_FROM, '')
        self.assertEqual(settings.FRONTEND_URL, 'http://localhost:4321')


class Slice23cLocalOutboxIntegrationTests(_Harness):
    """The SMTP_DEV_LOCAL_OUTBOX path is the integration-test friendly
    substitute for SMTP. A real email-rendered .eml file should appear
    there for one email mint — plus a .summary.txt next to it with the
    plaintext copy for grep-based test inspection.

    We decode the .eml mime part and assert on the rendered templates
    directly rather than substring-searching the base64."""
    async def test_email_drops_file_to_outbox(self):
        import email
        import os

        outbox = os.environ['SMTP_DEV_LOCAL_OUTBOX']
        before = set(os.listdir(outbox))

        cid = await self._create_circle('Real send')
        await self._mint(
            cid,
            delivery='email',
            invited_email='greg@example.com',
        )

        after = set(os.listdir(outbox))
        new_files = after - before
        self.assertGreater(len(new_files), 0, 'expected an .eml in the outbox')

        eml_files = [f for f in new_files if f.endswith('.eml')]
        self.assertGreater(len(eml_files), 0)
        sample = os.path.join(outbox, eml_files[0])
        with open(sample, encoding='utf-8') as fh:
            raw = fh.read()

        msg = email.message_from_string(raw)
        # Subject
        self.assertIn('Real send', msg['Subject'] or '')
        # To header carries the recipient.
        self.assertIn('greg@example.com', msg['To'] or '')
        # Walk MIME parts and assert the HTML carries the CTA button.
        html_found = False
        text_found = False
        for part in msg.walk():
            ctype = part.get_content_type()
            payload = part.get_payload(decode=True)
            # Pyright can't tell that walk() yields sub-Messages whose
            # decoded payload is bytes; runtime behaviour is correct.
            decoded = payload.decode('utf-8', errors='replace') if payload else ''  # type: ignore[union-attr]
            if ctype == 'text/plain' and decoded:
                text_found = True
                self.assertIn('Real send', decoded)
            elif ctype == 'text/html' and decoded:
                html_found = True
                self.assertIn('Accept invite', decoded)
                self.assertIn('Real send', decoded)
        self.assertTrue(html_found, 'expected an HTML MIME part')
        self.assertTrue(text_found, 'expected a text/plain MIME part')


if __name__ == '__main__':
    unittest.main()
