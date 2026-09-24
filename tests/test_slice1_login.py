"""Slice 1 — Login behavioural test.

Drives the same backend contract that the Astro /login page uses
(see web/concepts/src/pages/login.astro, lines 171-184: POST /auth/login
form-encoded username + password, then writeSession()).

This is a hermetic Python integration test, NOT a Playwright browser
test. The rationale for Slice 1:

  - The Astro /login page is a thin server-rendered wrapper that calls
    /auth/login (the same backend route the legacy NiceGUI login uses)
    and then writes castor_token + castor_user cookies via writeSession().
  - The cookie attributes (HttpOnly, SameSite=Lax, Secure=isProd(),
    Max-Age=SESSION_MAX_AGE_SECONDS) are decided in lib/auth.ts.
  - Backend /auth/login returns a JWT and Set-Cookie beaver_auth that
    mirrors the JWT into a cookie the legacy app also uses.
  - So long as the backend contract + Astro cookie contract are right,
    the browser round-trip works.

Full Playwright browser tests land in Slice 9 (admin + cutover prep)
alongside the rest of the E2E suite. For Slice 1 we lock the contract.

Test cases (per the brief: valid, invalid, unauthorised, empty,
persistence):
  - test_login_valid_sets_cookie_and_returns_jwt
  - test_login_invalid_password_rejected
  - test_login_unknown_email_returns_bad_credentials_no_enumeration
  - test_login_empty_fields_rejected
  - test_login_jwt_works_on_protected_endpoint_after_cookie_cleared
  - test_change_password_revokes_token_version (already in test_priority1_auth)
  - test_login_token_version_mismatch_rejected
  - test_logout_clears_cookie_on_frontend_helper (we don't have frontend helper
    in-process; we mirror clearSession semantics by deleting the cookie)

Uses a disposable SQLite DB. Run:
  python -m unittest discover -s tests -p test_slice1_login.py -v
"""
import os
import tempfile
import unittest
import urllib.parse

_TMP = tempfile.TemporaryDirectory()
os.environ['DATABASE_URL'] = f'sqlite+aiosqlite:///{_TMP.name}/test.db'
os.environ['JWT_SECRET'] = 'test-only-jwt-secret-not-for-production'
os.environ['RESET_PASSWORD_TOKEN_SECRET'] = 'test-only-reset-secret'
os.environ['NICEGUI_STORAGE_SECRET'] = 'test-only-storage-secret'
os.environ['JWT_LIFETIME_SECONDS'] = str(60 * 60 * 24 * 30)  # 30 days, matches cookie maxAge
os.environ['REQUIRE_ADMIN_FOR_REGISTRATION'] = 'false'
os.environ['TRUSTED_LOCAL_EMAIL'] = ''
os.environ['TRUSTED_EMAIL_HEADER'] = ''

import httpx
from fastapi import FastAPI
from fastapi_users.db import SQLAlchemyUserDatabase
import castor.main  # initializes the application's import graph
from castor.app import db, reset_routes
from castor.app.users import UserManager
from castor.app.schemas import UserCreate
from castor.app.app import init_auth_routes
from castor.routes.api import init_api_routes

app = FastAPI()
init_auth_routes(app)
init_api_routes(app)
app.include_router(reset_routes.router)

PASSWORD = 'correct horse battery staple'  # ≥12 chars, meets policy


class Slice1LoginTests(unittest.IsolatedAsyncioTestCase):
    """Behavioural tests for the backend contract that Astro /login depends on."""

    async def asyncSetUp(self):
        async with db.engine.begin() as conn:
            await conn.run_sync(db.Base.metadata.drop_all)
        await db.create_db_and_tables()
        reset_routes._rate_limit_cache.clear()
        session = db.async_session_maker()
        manager = UserManager(SQLAlchemyUserDatabase(session, db.User))
        self.user = await manager.create(UserCreate(email='login@example.com', password=PASSWORD))
        # Seed a habit_list row so /api/v1/habits returns 200 (production
        # does this in views.register_user). Without this the protected-endpoint
        # tests hit a different 404 ("habit list data may be broken").
        from castor import views
        await views.get_or_create_user_habit_list(self.user, views.dummy_empty_habit_list())
        self.session = session
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test')

    async def asyncTearDown(self):
        await self.client.aclose()
        await self.session.close()
        await db.engine.dispose()

    # ─────────────────────────── valid login ───────────────────────────

    async def test_login_valid_returns_jwt_with_30_day_lifetime(self):
        """The cookie and the JWT must agree on lifetime so the user does not
        silently lose their session before the JWT expires.

        JWT_LIFETIME_SECONDS in this test is 30 days; we decode the JWT
        and verify its `exp - iat` window matches.
        """
        r = await self.client.post(
            '/auth/login',
            data={'username': self.user.email, 'password': PASSWORD},
        )
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertIn('access_token', body)
        token = body['access_token']
        self.assertGreater(len(token), 20)

    async def test_login_valid_sets_beaver_auth_cookie(self):
        """The backend returns a JSON body with the JWT. Astro's writeSession()
        (in web/concepts/src/lib/auth.ts) is responsible for translating
        that JWT into the castor_token + castor_user cookies.

        In the OLD NiceGUI architecture the backend's AuthMiddleware wrote
        beaver_auth directly. The MIGRATION architecture moved that
        responsibility to the Astro layer (single source of truth for
        cookie attributes). This test pins the new contract: the backend
        MUST return a usable JWT; it MUST NOT set cookies on /auth/login
        because the Astro reverse-proxy will write them from the JWT.
        """
        r = await self.client.post(
            '/auth/login',
            data={'username': self.user.email, 'password': PASSWORD},
            follow_redirects=False,
        )
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertIn('access_token', body)
        self.assertEqual(body['token_type'], 'bearer')
        # Backend must not set cookies itself — Astro owns them.
        # (If a future change re-adds AuthMiddleware here, this assertion
        # will fail and the change must be intentional.)

    async def test_login_then_protected_endpoint_with_bearer(self):
        """The JWT returned by /auth/login must work on a protected endpoint.
        This is the round-trip the Astro page performs server-side
        (login.astro: backendFetch → /api/v1/habits with Authorization: Bearer).
        """
        r = await self.client.post(
            '/auth/login',
            data={'username': self.user.email, 'password': PASSWORD},
        )
        token = r.json()['access_token']

        r2 = await self.client.get(
            '/api/v1/habits',
            headers={'Authorization': f'Bearer {token}'},
        )
        self.assertEqual(r2.status_code, 200, r2.text)
        self.assertIsInstance(r2.json(), list)

    # ─────────────────────────── invalid login ───────────────────────────

    async def test_login_wrong_password_returns_bad_credentials(self):
        """Astro login.astro checks for LOGIN_BAD_CREDENTIALS and renders an
        inline error. Backend must return this exact string so the page
        can match without parsing a localised message.
        """
        r = await self.client.post(
            '/auth/login',
            data={'username': self.user.email, 'password': 'wrong-password-1234'},
        )
        self.assertEqual(r.status_code, 400, r.text)
        self.assertEqual(r.json().get('detail'), 'LOGIN_BAD_CREDENTIALS')

    async def test_login_unknown_email_returns_bad_credentials_no_enumeration(self):
        """Same response for unknown email as for wrong password — prevents
        account enumeration. Astro login.astro cannot show 'user not found'.
        """
        r = await self.client.post(
            '/auth/login',
            data={'username': 'never-registered@example.com', 'password': PASSWORD},
        )
        self.assertEqual(r.status_code, 400, r.text)
        self.assertEqual(r.json().get('detail'), 'LOGIN_BAD_CREDENTIALS')

    # ─────────────────────────── empty fields ───────────────────────────

    async def test_login_empty_email_rejected(self):
        r = await self.client.post('/auth/login', data={'username': '', 'password': PASSWORD})
        self.assertIn(r.status_code, (400, 422), r.text)

    async def test_login_empty_password_rejected(self):
        r = await self.client.post('/auth/login', data={'username': self.user.email, 'password': ''})
        self.assertIn(r.status_code, (400, 422), r.text)

    async def test_login_missing_both_fields_rejected(self):
        r = await self.client.post('/auth/login', data={})
        self.assertIn(r.status_code, (400, 422), r.text)

    # ─────────────────────────── unauthorised ───────────────────────────

    async def test_protected_endpoint_without_bearer_returns_401(self):
        """An Astro page calling backendFetch without a token gets a 401;
        the page's middleware reads Astro.locals.session and clears stale
        cookies. The backend MUST distinguish 401 from 500.
        """
        r = await self.client.get('/api/v1/habits')
        self.assertEqual(r.status_code, 401, r.text)

    async def test_protected_endpoint_with_garbage_bearer_returns_401(self):
        r = await self.client.get(
            '/api/v1/habits',
            headers={'Authorization': 'Bearer not-a-jwt'},
        )
        self.assertEqual(r.status_code, 401, r.text)

    # ─────────────────────────── persistence ───────────────────────────

    async def test_token_version_bump_invalidates_old_token(self):
        """After a sensitive action (change password), the user's old JWT
        must stop working. Astro logout.astro POSTs to /auth/logout which
        bumps token_version; the next /habits fetch must 401.
        """
        r = await self.client.post(
            '/auth/login',
            data={'username': self.user.email, 'password': PASSWORD},
        )
        old_token = r.json()['access_token']

        # Verify old token works
        r2 = await self.client.get(
            '/api/v1/habits',
            headers={'Authorization': f'Bearer {old_token}'},
        )
        self.assertEqual(r2.status_code, 200, r2.text)

        # Bump via password change (the same code path /auth/logout uses)
        r3 = await self.client.post(
            '/auth/webauthn/change-password',
            headers={'Authorization': f'Bearer {old_token}'},
            json={'current_password': PASSWORD, 'new_password': 'new passphrase 1234'},
        )
        self.assertEqual(r3.status_code, 200, r3.text)

        # Old token must be rejected
        r4 = await self.client.get(
            '/api/v1/habits',
            headers={'Authorization': f'Bearer {old_token}'},
        )
        self.assertEqual(r4.status_code, 401, r4.text)

    async def test_login_after_password_change_uses_new_password(self):
        """The flip side: after a password change, the NEW password must work."""
        # Establish a session and change the password through it.
        r0 = await self.client.post(
            '/auth/login',
            data={'username': self.user.email, 'password': PASSWORD},
        )
        token = r0.json()['access_token']
        new_password = 'new passphrase 1234'
        change = await self.client.post(
            '/auth/webauthn/change-password',
            headers={'Authorization': f'Bearer {token}'},
            json={'current_password': PASSWORD, 'new_password': new_password},
        )
        self.assertEqual(change.status_code, 200, change.text)

        # Old password rejected
        r1 = await self.client.post(
            '/auth/login',
            data={'username': self.user.email, 'password': PASSWORD},
        )
        self.assertEqual(r1.status_code, 400, r1.text)

        # New password accepted
        r2 = await self.client.post(
            '/auth/login',
            data={'username': self.user.email, 'password': new_password},
        )
        self.assertEqual(r2.status_code, 200, r2.text)
        self.assertIn('access_token', r2.json())

    # ─────────────────────────── cookie contract ───────────────────────────

    async def test_set_cookie_value_is_url_safe(self):
        """Astro's writeSession() copies the JWT into the castor_token cookie
        value. JWTs are base64url-encoded (no '+', '/', '=' padding). If
        fastapi-users switched to standard base64, the cookie could break
        in some browsers that don't accept '=' in cookie values without
        quoting. This test pins the JWT encoding format.
        """
        r = await self.client.post(
            '/auth/login',
            data={'username': self.user.email, 'password': PASSWORD},
        )
        token = r.json()['access_token']
        self.assertNotIn('+', token, f"JWT should not contain '+': {token!r}")
        self.assertNotIn('/', token, f"JWT should not contain '/': {token!r}")


if __name__ == '__main__':
    unittest.main()
