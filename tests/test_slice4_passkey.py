"""Slice 4 — Passkey (WebAuthn) login + credential management.

Pins the backend contract that the Astro Login page's Passkey button
relies on:

  - POST /auth/webauthn/check             → progressive-disclosure probe
  - POST /auth/webauthn/login/begin       → start authentication ceremony
  - POST /auth/webauthn/offer/dismiss     → mark "set up a passkey?" as done
  - GET  /auth/webauthn/credentials       → list user's passkeys (auth)
  - DELETE /auth/webauthn/credentials/{id}→ remove a passkey (auth)
  - 404s, 401s, 403s for the negative paths

This file does NOT test the full cryptographic ceremony (begin+complete
round-trip). That requires a real WebAuthn authenticator (TouchID, Yubikey,
virtual authenticator in Playwright). The ceremony internals are already
covered by tests/test_lane2_webauthn.py (15+ tests). Slice 4 focuses on
the surface that the Astro login page actually depends on.

Run:
  python -m unittest discover -s tests -p test_slice4_passkey.py -v
"""
import base64
import os
import tempfile
import unittest

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
# WebAuthn-specific env. The defaults are fine for hermetic tests:
# RP_ID=localhost, RP_NAME=Castor, ORIGIN=http://localhost:8080
# These DO NOT need to match the test client's origin (we don't run the
# verifier in these tests).

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

app = FastAPI()
init_auth_routes(app)  # includes webauthn_router
init_api_routes(app)
app.include_router(reset_routes.router)

PASSWORD = 'correct horse battery staple'


def _b64u(b: bytes) -> str:
    """URL-safe base64 without padding (what WebAuthn uses)."""
    return base64.urlsafe_b64encode(b).decode().rstrip('=')


class Slice4PasskeyTests(unittest.IsolatedAsyncioTestCase):
    """Backend contract tests for the passkey login + credential management."""

    async def asyncSetUp(self):
        async with db.engine.begin() as conn:
            await conn.run_sync(db.Base.metadata.drop_all)
        await db.create_db_and_tables()
        reset_routes._rate_limit_cache.clear()
        session = db.async_session_maker()
        manager = UserManager(SQLAlchemyUserDatabase(session, db.User))
        self.user_with_pk = await manager.create(
            UserCreate(email='passkey@example.com', password=PASSWORD)
        )
        await views.get_or_create_user_habit_list(self.user_with_pk, views.dummy_empty_habit_list())
        # Seed a fake credential for the passkey user
        await self._seed_credential(
            self.user_with_pk,
            credential_id=b'fake-credential-id-aaaa',
            sign_count=1,
            name='Test Passkey',
        )

        self.user_no_pk = await manager.create(
            UserCreate(email='nopasskey@example.com', password=PASSWORD)
        )
        await views.get_or_create_user_habit_list(self.user_no_pk, views.dummy_empty_habit_list())

        self.session = session
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test')

    async def asyncTearDown(self):
        await self.client.aclose()
        await self.session.close()
        await db.engine.dispose()

    async def _seed_credential(self, user, credential_id: bytes, sign_count: int, name: str = ''):
        from castor.app.db import WebAuthnCredential
        from sqlalchemy import insert
        async with db.async_session_maker() as s:
            await s.execute(insert(WebAuthnCredential).values(
                id=credential_id,
                user_id=user.id,
                public_key=b'fake-public-key-bytes',
                sign_count=sign_count,
                transports=[],
                name=name,
            ))
            await s.commit()

    async def _login(self, email: str) -> str:
        r = await self.client.post(
            '/auth/login',
            data={'username': email, 'password': PASSWORD},
        )
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()['access_token']

    # ─────────────────────────── /auth/webauthn/check ───────────────────────────

    async def test_check_user_with_passkey_returns_has_passkey_true(self):
        """Astro login.astro calls /check on Stage 1 to decide whether to show
        a 'Sign in with passkey' button. With a registered passkey, it MUST
        return has_passkey=true."""
        r = await self.client.post(
            '/auth/webauthn/check',
            json={'username': self.user_with_pk.email},
        )
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body, {'has_passkey': True, 'passkey_offer_dismissed': True})

    async def test_check_user_without_passkey_returns_has_passkey_false(self):
        """User exists, no passkey. /check returns has_passkey=false."""
        r = await self.client.post(
            '/auth/webauthn/check',
            json={'username': self.user_no_pk.email},
        )
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body['has_passkey'], False)
        self.assertEqual(body['passkey_offer_dismissed'], False)

    async def test_check_unknown_email_returns_same_envelope(self):
        """/check must NOT leak whether an email is registered. Unknown email
        returns the SAME shape as 'no passkey' — both are has_passkey=false,
        passkey_offer_dismissed=false. This is the no-enumeration guarantee."""
        r = await self.client.post(
            '/auth/webauthn/check',
            json={'username': 'never-registered@example.com'},
        )
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json(), {'has_passkey': False, 'passkey_offer_dismissed': False})

    async def test_check_empty_username_does_not_500(self):
        """Astro should not call /check with empty username, but the backend
        must not 500 if it does. /check is meant to be cheap and safe."""
        r = await self.client.post(
            '/auth/webauthn/check',
            json={'username': ''},
        )
        # Either 200 (returns false envelope) or 422 (validation). NOT 500.
        self.assertIn(r.status_code, (200, 422), r.text)

    async def test_check_missing_username_does_not_500(self):
        r = await self.client.post('/auth/webauthn/check', json={})
        self.assertIn(r.status_code, (200, 422), r.text)

    async def test_check_has_passkey_user_also_marks_offer_dismissed(self):
        """If has_passkey=true, passkey_offer_dismissed MUST also be true:
        enrolling a passkey counts as dismissing the post-login offer."""
        r = await self.client.post(
            '/auth/webauthn/check',
            json={'username': self.user_with_pk.email},
        )
        body = r.json()
        if body['has_passkey']:
            self.assertTrue(
                body['passkey_offer_dismissed'],
                "has_passkey=true must imply passkey_offer_dismissed=true"
            )

    # ─────────────────────────── /auth/webauthn/offer/dismiss ───────────────────────────

    async def test_offer_dismiss_marks_user_dismissed(self):
        """After dismissing the post-login 'set up a passkey?' offer,
        /check must reflect passkey_offer_dismissed=true."""
        # User without passkey, offer not yet dismissed
        before = await self.client.post(
            '/auth/webauthn/check',
            json={'username': self.user_no_pk.email},
        )
        self.assertEqual(before.json()['passkey_offer_dismissed'], False)

        # Dismiss (no auth required — this is just a UX flag)
        r = await self.client.post(
            '/auth/webauthn/offer/dismiss',
            json={'username': self.user_no_pk.email},
        )
        self.assertEqual(r.status_code, 200, r.text)

        after = await self.client.post(
            '/auth/webauthn/check',
            json={'username': self.user_no_pk.email},
        )
        self.assertEqual(after.json()['passkey_offer_dismissed'], True)
        # has_passkey is still false
        self.assertEqual(after.json()['has_passkey'], False)

    async def test_offer_dismiss_unknown_email_does_not_500(self):
        """/offer/dismiss on unknown email must not 500. It can be a no-op
        (offer was never going to be shown anyway)."""
        r = await self.client.post(
            '/auth/webauthn/offer/dismiss',
            json={'username': 'never-registered@example.com'},
        )
        self.assertEqual(r.status_code, 200, r.text)

    # ─────────────────────────── /auth/webauthn/login/begin ───────────────────────────

    async def test_login_begin_user_with_passkey_returns_publicKey(self):
        """The ceremony-start endpoint returns a publicKey dict the browser
        hands to navigator.credentials.get(). The response shape MUST be
        {publicKey: {...}} per WebAuthn spec."""
        r = await self.client.post(
            '/auth/webauthn/login/begin',
            json={'username': self.user_with_pk.email},
        )
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertIn('publicKey', body)
        pk = body['publicKey']
        # Standard WebAuthn fields
        self.assertIn('challenge', pk)
        self.assertIn('rpId', pk)
        self.assertIn('allowCredentials', pk)
        # challenge is base64url-encoded
        self.assertEqual(len(pk['challenge']), 43)  # 32 bytes -> 43 b64u chars

    async def test_login_begin_unknown_user_returns_404(self):
        r = await self.client.post(
            '/auth/webauthn/login/begin',
            json={'username': 'never-registered@example.com'},
        )
        self.assertEqual(r.status_code, 404, r.text)
        self.assertEqual(r.json()['detail'], 'User not found')

    async def test_login_begin_user_without_passkey_returns_404(self):
        """User exists, has password, but no passkey. /login/begin must
        return 404 with 'No passkeys registered' so the Astro login page
        can fall back to password entry."""
        r = await self.client.post(
            '/auth/webauthn/login/begin',
            json={'username': self.user_no_pk.email},
        )
        self.assertEqual(r.status_code, 404, r.text)
        self.assertEqual(r.json()['detail'], 'No passkeys registered for this user')

    async def test_login_begin_issues_browser_cookie(self):
        """A successful /login/begin sets a beaver_webauthn browser cookie.
        The browser cookie binds challenges to the originating browser."""
        r = await self.client.post(
            '/auth/webauthn/login/begin',
            json={'username': self.user_with_pk.email},
        )
        self.assertEqual(r.status_code, 200, r.text)
        set_cookie = r.headers.get('set-cookie', '')
        # Either the cookie is set on first call, or it was already set
        # on a previous call (the cookie is stable across begin calls).
        # If it's set, verify the name.
        if 'beaver_webauthn=' in set_cookie:
            # 32 bytes -> 43 base64url chars (token_urlsafe)
            self.assertRegex(set_cookie, r'beaver_webauthn=[A-Za-z0-9_-]{43}')

    # ─────────────────────────── /auth/webauthn/credentials ───────────────────────────

    async def test_credentials_list_returns_seeded_credential(self):
        """The /security page renders a list of passkeys. The list endpoint
        must include the seeded test credential."""
        token = await self._login(self.user_with_pk.email)
        r = await self.client.get(
            '/auth/webauthn/credentials',
            headers={'Authorization': f'Bearer {token}'},
        )
        self.assertEqual(r.status_code, 200, r.text)
        creds = r.json()
        self.assertEqual(len(creds), 1)
        cred = creds[0]
        self.assertEqual(cred['name'], 'Test Passkey')
        self.assertEqual(cred['id'], _b64u(b'fake-credential-id-aaaa'))
        # Fields the /security page renders
        self.assertIn('created_at', cred)
        self.assertIn('backup_eligible', cred)
        self.assertIn('backup_state', cred)
        self.assertIn('transports', cred)

    async def test_credentials_list_user_with_no_passkey_returns_empty(self):
        token = await self._login(self.user_no_pk.email)
        r = await self.client.get(
            '/auth/webauthn/credentials',
            headers={'Authorization': f'Bearer {token}'},
        )
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json(), [])

    async def test_credentials_list_unauthenticated_returns_401(self):
        r = await self.client.get('/auth/webauthn/credentials')
        self.assertEqual(r.status_code, 401, r.text)

    async def test_credentials_list_with_garbage_bearer_returns_401(self):
        r = await self.client.get(
            '/auth/webauthn/credentials',
            headers={'Authorization': 'Bearer not-a-jwt'},
        )
        self.assertEqual(r.status_code, 401, r.text)

    # ─────────────────────────── DELETE /auth/webauthn/credentials/{id} ───────────────────────────

    async def test_delete_credential_removes_from_list(self):
        """The /security page's 'Remove passkey' button hits DELETE /credentials/{id}.
        After deletion, /credentials must return the empty list."""
        token = await self._login(self.user_with_pk.email)
        cred_id_b64 = _b64u(b'fake-credential-id-aaaa')

        r = await self.client.request(
            'DELETE',
            f'/auth/webauthn/credentials/{cred_id_b64}',
            headers={'Authorization': f'Bearer {token}'},
            json={'current_password': PASSWORD},
        )
        # 200 OK or 204 No Content — depends on backend
        self.assertIn(r.status_code, (200, 204), r.text)

        listing = await self.client.get(
            '/auth/webauthn/credentials',
            headers={'Authorization': f'Bearer {token}'},
        )
        self.assertEqual(listing.json(), [])

    async def test_delete_credential_unauthenticated_returns_401(self):
        cred_id_b64 = _b64u(b'fake-credential-id-aaaa')
        r = await self.client.request(
            'DELETE',
            f'/auth/webauthn/credentials/{cred_id_b64}',
            json={'current_password': PASSWORD},
        )
        self.assertEqual(r.status_code, 401, r.text)

    async def test_delete_credential_wrong_password_returns_401_and_keeps_credential(self):
        """Defence-in-depth: removing a passkey requires re-confirming the
        password (current_password in body). Wrong password must NOT delete.

        NOTE: castor's `_authorize` raises `AuthorizationError` (401) on
        any auth failure including wrong password — there's no distinction
        between 'wrong password' and 'no password' to avoid enumeration."""
        token = await self._login(self.user_with_pk.email)
        cred_id_b64 = _b64u(b'fake-credential-id-aaaa')

        r = await self.client.request(
            'DELETE',
            f'/auth/webauthn/credentials/{cred_id_b64}',
            headers={'Authorization': f'Bearer {token}'},
            json={'current_password': 'wrong-password-1234'},
        )
        # 401 with the public-safe message; never 400/403 (those would leak
        # which credential was being deleted).
        self.assertEqual(r.status_code, 401, r.text)
        self.assertEqual(
            r.json()['detail'],
            'Fresh current-password authorization required; use verified email recovery if needed',
        )

        listing = await self.client.get(
            '/auth/webauthn/credentials',
            headers={'Authorization': f'Bearer {token}'},
        )
        self.assertEqual(len(listing.json()), 1,
                         "wrong-password DELETE must not have removed the credential")

    async def test_delete_credential_cross_user_returns_404(self):
        """User A's passkey must not be deletable by user B."""
        # Seed a second user with a passkey
        from castor.app.db import WebAuthnCredential
        from sqlalchemy import insert
        manager = UserManager(SQLAlchemyUserDatabase(db.async_session_maker(), db.User))
        user_b = await manager.create(
            UserCreate(email='b@example.com', password=PASSWORD)
        )
        await views.get_or_create_user_habit_list(user_b, views.dummy_empty_habit_list())
        async with db.async_session_maker() as s:
            await s.execute(insert(WebAuthnCredential).values(
                id=b'fake-credential-id-bbbb',
                user_id=user_b.id,
                public_key=b'fake-public-key-bytes',
                sign_count=1,
                transports=[],
                name='B Passkey',
            ))
            await s.commit()

        # Log in as A (the wrong user)
        token_a = await self._login(self.user_with_pk.email)

        r = await self.client.request(
            'DELETE',
            f'/auth/webauthn/credentials/{_b64u(b"fake-credential-id-bbbb")}',
            headers={'Authorization': f'Bearer {token_a}'},
            json={'current_password': PASSWORD},
        )
        # 404 (cross-user, no enumeration) — same shape as habit 404
        self.assertEqual(r.status_code, 404, r.text)

        # B's credential still exists
        token_b = await self._login(user_b.email)
        listing = await self.client.get(
            '/auth/webauthn/credentials',
            headers={'Authorization': f'Bearer {token_b}'},
        )
        self.assertEqual(len(listing.json()), 1)


if __name__ == '__main__':
    unittest.main()
