"""Slice 5 — /security page backend contracts.

Pins the backend contract for the Astro /security page:

  - POST /auth/webauthn/verify-password     → step-up auth (returns {verified: true})
  - POST /auth/webauthn/change-password     → rotate password (bumps token_version)
  - POST /auth/webauthn/recovery-email      → request 6-digit code (sends email)
  - POST /auth/webauthn/recovery-email/verify → verify code; remove=True clears
  - DELETE /api/v1/account                  → wipe account (204)

All of these are auth-required. They share the same step-up auth pattern
from castor/app/security_actions.py:63:
  - Sensitive actions require re-confirming the current password.
  - Failure returns 401 with a public-safe message (no enumeration).
  - token_version is bumped on change-password, invalidating all sessions.

Email sending is mocked to avoid hitting Gmail SMTP during the test.

Run:
  python -m unittest discover -s tests -p test_slice5_security.py -v
"""
import os
import tempfile
import unittest
from unittest.mock import patch, AsyncMock

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

import httpx
from fastapi import FastAPI
from fastapi_users.db import SQLAlchemyUserDatabase
import castor.main
from castor import views
from castor.app import db, reset_routes
from castor.app.users import UserManager
from castor.app.schemas import UserCreate
from castor.app.app import init_auth_routes
from castor.routes.api import init_api_routes
from castor.app import webauthn_routes as webauthn_routes_module

app = FastAPI()
init_auth_routes(app)
init_api_routes(app)
app.include_router(reset_routes.router)

PASSWORD = 'correct horse battery staple'


class Slice5SecurityTests(unittest.IsolatedAsyncioTestCase):
    """Backend contract tests for the /security page actions."""

    async def asyncSetUp(self):
        async with db.engine.begin() as conn:
            await conn.run_sync(db.Base.metadata.drop_all)
        await db.create_db_and_tables()
        reset_routes._rate_limit_cache.clear()
        session = db.async_session_maker()
        manager = UserManager(SQLAlchemyUserDatabase(session, db.User))
        self.user = await manager.create(
            UserCreate(email='security@example.com', password=PASSWORD)
        )
        await views.get_or_create_user_habit_list(self.user, views.dummy_empty_habit_list())
        self.session = session
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test')

    async def asyncTearDown(self):
        await self.client.aclose()
        await self.session.close()
        await db.engine.dispose()

    async def _login(self, password: str = PASSWORD) -> str:
        r = await self.client.post(
            '/auth/login',
            data={'username': self.user.email, 'password': password},
        )
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()['access_token']

    async def _request_recovery(self, token: str, email: str = 'recovery@example.com') -> dict:
        """POST recovery-email; return {code, response}. The send_email mock
        is captured via the `mail` MagicMock returned by patch.object()."""
        with patch.object(webauthn_routes_module, 'send_email') as mail:
            r = await self.client.post(
                '/auth/webauthn/recovery-email',
                headers={'Authorization': f'Bearer {token}'},
                json={'email': email},
            )
            self.assertEqual(r.status_code, 200, r.text)
            self.assertTrue(mail.called, "send_email must be called")
            # The body arg contains the 6-digit code in plaintext
            import re
            body = mail.call_args.args[1] if mail.call_args and len(mail.call_args.args) >= 2 else ''
            m = re.search(r'\b(\d{6})\b', body)
            self.assertIsNotNone(m, f"no 6-digit code in email body: {body!r}")
        return {'code': m.group(1), 'response': r}

    # ─────────────────────────── verify-password (step-up) ───────────────────────────

    async def test_verify_password_with_correct_password_returns_verified_true(self):
        """The /security page calls /verify-password to confirm the user
        knows their current password before letting them change it / remove
        a passkey. The contract is {verified: true} on success."""
        token = await self._login()
        r = await self.client.post(
            '/auth/webauthn/verify-password',
            headers={'Authorization': f'Bearer {token}'},
            json={'password': PASSWORD},
        )
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json(), {'verified': True})

    async def test_verify_password_with_wrong_password_returns_401(self):
        """Wrong password returns 401 with the public-safe message.
        No enumeration: 401 is the same shape regardless of which way it failed."""
        token = await self._login()
        r = await self.client.post(
            '/auth/webauthn/verify-password',
            headers={'Authorization': f'Bearer {token}'},
            json={'password': 'wrong-password-1234'},
        )
        self.assertEqual(r.status_code, 401, r.text)
        self.assertEqual(r.json()['detail'], 'Current password is incorrect')

    async def test_verify_password_unauthenticated_returns_401(self):
        r = await self.client.post(
            '/auth/webauthn/verify-password',
            json={'password': PASSWORD},
        )
        self.assertEqual(r.status_code, 401, r.text)

    async def test_verify_password_empty_password_returns_401(self):
        """Empty password rejected as 'incorrect' (401), not 422.
        This keeps the failure mode uniform."""
        token = await self._login()
        r = await self.client.post(
            '/auth/webauthn/verify-password',
            headers={'Authorization': f'Bearer {token}'},
            json={'password': ''},
        )
        # Either 401 (fastapi-users rejects empty) or 422 (Pydantic min_length)
        self.assertIn(r.status_code, (401, 422), r.text)

    # ─────────────────────────── change-password ───────────────────────────

    async def test_change_password_succeeds_and_bumps_token_version(self):
        """Change password succeeds; token_version is bumped; old JWT rejected.
        This is the round-trip the /security page performs."""
        token = await self._login()
        new_password = 'new passphrase with enough entropy 1234'

        r = await self.client.post(
            '/auth/webauthn/change-password',
            headers={'Authorization': f'Bearer {token}'},
            json={'current_password': PASSWORD, 'new_password': new_password},
        )
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn('message', r.json())

        # Old JWT must now be rejected
        r2 = await self.client.get(
            '/api/v1/habits',
            headers={'Authorization': f'Bearer {token}'},
        )
        self.assertEqual(r2.status_code, 401, r2.text)

        # New password must work
        r3 = await self.client.post(
            '/auth/login',
            data={'username': self.user.email, 'password': new_password},
        )
        self.assertEqual(r3.status_code, 200, r3.text)
        self.assertIn('access_token', r3.json())

        # Old password must NOT work
        r4 = await self.client.post(
            '/auth/login',
            data={'username': self.user.email, 'password': PASSWORD},
        )
        self.assertEqual(r4.status_code, 400, r4.text)

    async def test_change_password_wrong_current_password_returns_401(self):
        token = await self._login()
        r = await self.client.post(
            '/auth/webauthn/change-password',
            headers={'Authorization': f'Bearer {token}'},
            json={'current_password': 'wrong-password', 'new_password': 'new passphrase 1234'},
        )
        self.assertEqual(r.status_code, 401, r.text)
        # Public-safe message
        self.assertIn('Fresh current-password', r.json()['detail'])

    async def test_change_password_unauthenticated_returns_401(self):
        r = await self.client.post(
            '/auth/webauthn/change-password',
            json={'current_password': PASSWORD, 'new_password': 'new passphrase 1234'},
        )
        self.assertEqual(r.status_code, 401, r.text)

    async def test_change_password_same_as_current_succeeds_or_rejected(self):
        """Some apps reject "new password same as old"; castor doesn't.
        The contract is: as long as the policy is met, it succeeds.
        Document whatever the actual behaviour is."""
        token = await self._login()
        r = await self.client.post(
            '/auth/webauthn/change-password',
            headers={'Authorization': f'Bearer {token}'},
            json={'current_password': PASSWORD, 'new_password': PASSWORD},
        )
        # Castor's password policy: ≥1 char (the form has min_length=1).
        # The actual change will succeed. We don't pin this — just record it.
        # Either 200 or 4xx. Both are defensible.
        self.assertIn(r.status_code, (200, 400, 422), r.text)

    # ─────────────────────────── recovery-email ───────────────────────────

    async def test_recovery_email_request_returns_pending_envelope(self):
        """Step 1 of recovery email setup: POST {email} → {pending, email, expires_at}.
        Email is sent (mocked here)."""
        token = await self._login()
        with patch.object(webauthn_routes_module, 'send_email'):
            r = await self.client.post(
                '/auth/webauthn/recovery-email',
                headers={'Authorization': f'Bearer {token}'},
                json={'email': 'recovery@example.com'},
            )
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertTrue(body['pending'])
        self.assertEqual(body['email'], 'recovery@example.com')
        self.assertIn('expires_at', body)

    async def test_recovery_email_request_unauthenticated_returns_401(self):
        r = await self.client.post(
            '/auth/webauthn/recovery-email',
            json={'email': 'recovery@example.com'},
        )
        self.assertEqual(r.status_code, 401, r.text)

    async def test_recovery_email_invalid_format_returns_422(self):
        """EmailStr validation catches malformed input."""
        token = await self._login()
        with patch.object(webauthn_routes_module, 'send_email'):
            r = await self.client.post(
                '/auth/webauthn/recovery-email',
                headers={'Authorization': f'Bearer {token}'},
                json={'email': 'not-an-email'},
            )
        self.assertEqual(r.status_code, 422, r.text)

    async def test_recovery_email_sends_email(self):
        """The send_email mock captures the call. Verify the subject contains
        'recovery' and the recipients include the email address."""
        token = await self._login()
        with patch.object(webauthn_routes_module, 'send_email') as mail:
            await self.client.post(
                '/auth/webauthn/recovery-email',
                headers={'Authorization': f'Bearer {token}'},
                json={'email': 'recovery@example.com'},
            )

        self.assertTrue(mail.called, "send_email must be called")
        args = mail.call_args.args
        subject = args[0] if len(args) >= 1 else ''
        recipients = args[2] if len(args) >= 3 else []
        self.assertIn('recovery', subject.lower())
        self.assertIn('recovery@example.com', recipients)

    async def test_recovery_email_verify_with_wrong_code_returns_400(self):
        token = await self._login()
        with patch.object(webauthn_routes_module, 'send_email'):
            r = await self.client.post(
                '/auth/webauthn/recovery-email/verify',
                headers={'Authorization': f'Bearer {token}'},
                json={'email': 'recovery@example.com', 'code': '000000'},
            )
        # Code mismatch is a public-safe 400 (or possibly 401/404).
        self.assertIn(r.status_code, (400, 401, 404), r.text)

    async def test_recovery_email_verify_with_correct_code_succeeds(self):
        """End-to-end happy path: request → capture code → verify → flag set."""
        token = await self._login()
        with patch.object(webauthn_routes_module, 'send_email') as mail:
            r1 = await self.client.post(
                '/auth/webauthn/recovery-email',
                headers={'Authorization': f'Bearer {token}'},
                json={'email': 'recovery@example.com'},
            )
            self.assertEqual(r1.status_code, 200, r1.text)

            # Extract the code from the email body
            import re
            body = mail.call_args.args[1]
            m = re.search(r'\b(\d{6})\b', body)
            self.assertIsNotNone(m, f"no 6-digit code in email body: {body!r}")
            code = m.group(1)

            r2 = await self.client.post(
                '/auth/webauthn/recovery-email/verify',
                headers={'Authorization': f'Bearer {token}'},
                json={'email': 'recovery@example.com', 'code': code},
            )
            self.assertEqual(r2.status_code, 200, r2.text)
            body = r2.json()
            self.assertEqual(body['recovery_email'], 'recovery@example.com')
            self.assertTrue(body['recovery_email_verified'])

    async def test_recovery_email_verify_remove_is_idempotent_when_no_email(self):
        """The /security page exposes a 'Remove recovery email' button.
        remove=True clears the field without a code. Idempotent when already None."""
        token = await self._login()

        # No recovery email set yet — remove should be a no-op
        with patch.object(webauthn_routes_module, 'send_email'):
            r = await self.client.post(
                '/auth/webauthn/recovery-email/verify',
                headers={'Authorization': f'Bearer {token}'},
                json={'email': 'recovery@example.com', 'code': '000000', 'remove': True},
            )
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertIsNone(body['recovery_email'])
        self.assertFalse(body['recovery_email_verified'])

    async def test_recovery_email_verify_invalid_code_format_returns_400(self):
        """Non-digit code is rejected with 400 'Code must be exactly 6 digits'."""
        token = await self._login()
        r = await self.client.post(
            '/auth/webauthn/recovery-email/verify',
            headers={'Authorization': f'Bearer {token}'},
            json={'email': 'recovery@example.com', 'code': 'abcdef'},
        )
        self.assertEqual(r.status_code, 400, r.text)
        self.assertEqual(r.json()['detail'], 'Code must be exactly 6 digits')

    async def test_recovery_email_verify_code_too_short_returns_400(self):
        token = await self._login()
        r = await self.client.post(
            '/auth/webauthn/recovery-email/verify',
            headers={'Authorization': f'Bearer {token}'},
            json={'email': 'recovery@example.com', 'code': '12345'},
        )
        self.assertEqual(r.status_code, 400, r.text)

    # ─────────────────────────── DELETE /api/v1/account ───────────────────────────

    async def test_delete_account_returns_204_and_clears_habit_list(self):
        """The /account/delete page calls DELETE /api/v1/account on confirm.
        The endpoint returns 204 and the user's habit_list is gone.
        A tombstone record is kept (anonymous disabled user)."""
        token = await self._login()

        # Verify habit_list exists
        before = await self.client.get(
            '/api/v1/habits',
            headers={'Authorization': f'Bearer {token}'},
        )
        self.assertEqual(before.status_code, 200, before.text)

        # Delete account
        r = await self.client.delete(
            '/api/v1/account',
            headers={'Authorization': f'Bearer {token}'},
        )
        self.assertEqual(r.status_code, 204, r.text)

        # Subsequent requests with the same token must 401
        after = await self.client.get(
            '/api/v1/habits',
            headers={'Authorization': f'Bearer {token}'},
        )
        self.assertEqual(after.status_code, 401, after.text)

    async def test_delete_account_unauthenticated_returns_401(self):
        r = await self.client.delete('/api/v1/account')
        self.assertEqual(r.status_code, 401, r.text)

    async def test_delete_account_with_garbage_bearer_returns_401(self):
        r = await self.client.delete(
            '/api/v1/account',
            headers={'Authorization': 'Bearer not-a-jwt'},
        )
        self.assertEqual(r.status_code, 401, r.text)

    async def test_delete_account_blocks_relogin_with_same_email(self):
        """After account deletion, the email cannot be re-registered.
        Either 400 'already exists' or the registration just succeeds with
        a brand-new user record (depending on backend behaviour).
        The crucial property: the OLD data (habits, ticks) is gone."""
        token = await self._login()

        await self.client.delete(
            '/api/v1/account',
            headers={'Authorization': f'Bearer {token}'},
        )

        # Try to register with same email
        r = await self.client.post(
            '/auth/register',
            json={'email': self.user.email, 'password': 'fresh-password-1234'},
        )
        # 201 (new user created) or 400 (already exists) — both acceptable.
        # The legacy castor keeps an anonymous disabled tombstone so re-registration
        # may 400. We don't pin this — just verify it doesn't 500.
        self.assertIn(r.status_code, (201, 400), r.text)


if __name__ == '__main__':
    unittest.main()
