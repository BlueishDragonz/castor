"""Slice 7 — /help + /tokens + /settings + /account/delete backend contracts.

Slice 7 has two halves:

1. **/help + /settings + /account/delete**: backend contracts are already
   pinned by Slices 5 and 6 (the GET /security/* and DELETE /api/v1/account
   endpoints). The work here is mostly Astro UI (page creation).
   tests/test_slice5_security.py + tests/test_slice6_import_export.py
   already cover the backend surface.

2. **/tokens (parity row 12.1)**: backend CRUD exists in
   castor/app/crud.py but NO HTTP route exposed it. This slice adds
   `GET/POST/POST(tokens/rotate)/DELETE /api/v1/tokens` and pins the
   contract with the tests below.

Tests in this file:

  /api/v1/tokens:
    - GET when no token exists → null
    - POST creates a new token and returns raw (only time)
    - GET returns masked form (raw never leaks after create)
    - POST/tokens/rotate invalidates old token immediately
    - DELETE returns 204 and old token now 401
    - 401 without bearer; 401 with garbage bearer
    - Rate-limited (covered by /api/v1 group limit, but we don't pin)
"""
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
init_auth_routes(app)
init_api_routes(app)
app.include_router(reset_routes.router)

PASSWORD = 'correct horse battery staple'


class Slice7TokensTests(unittest.IsolatedAsyncioTestCase):
    """Backend contract tests for /api/v1/tokens."""

    async def asyncSetUp(self):
        async with db.engine.begin() as conn:
            await conn.run_sync(db.Base.metadata.drop_all)
        await db.create_db_and_tables()
        reset_routes._rate_limit_cache.clear()
        session = db.async_session_maker()
        manager = UserManager(SQLAlchemyUserDatabase(session, db.User))
        self.user = await manager.create(
            UserCreate(email='tokens@example.com', password=PASSWORD)
        )
        await views.get_or_create_user_habit_list(self.user, views.dummy_empty_habit_list())
        self.session = session
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test')

    async def asyncTearDown(self):
        await self.client.aclose()
        await self.session.close()
        await db.engine.dispose()

    async def _login(self) -> str:
        r = await self.client.post(
            '/auth/login',
            data={'username': self.user.email, 'password': PASSWORD},
        )
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()['access_token']

    # ─────────────────────────── GET /api/v1/tokens ───────────────────────────

    async def test_get_tokens_no_token_returns_null(self):
        """User has never created an API token. GET returns null (not 404)."""
        token = await self._login()
        r = await self.client.get(
            '/api/v1/tokens',
            headers={'Authorization': f'Bearer {token}'},
        )
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json(), {'token': None})

    async def test_get_tokens_returns_masked_form(self):
        """After POST, GET returns the masked form: ``abc12345...wxyz``.
        The raw token is NEVER leaked via GET."""
        token = await self._login()

        # Create
        create = await self.client.post(
            '/api/v1/tokens',
            headers={'Authorization': f'Bearer {token}'},
        )
        self.assertEqual(create.status_code, 201, create.text)
        raw = create.json()['token']
        self.assertGreater(len(raw), 30)  # secrets.token_urlsafe(32) → 43 chars

        # GET returns masked
        get_resp = await self.client.get(
            '/api/v1/tokens',
            headers={'Authorization': f'Bearer {token}'},
        )
        self.assertEqual(get_resp.status_code, 200, get_resp.text)
        masked = get_resp.json()['token']
        # Masked form: 8 chars + "..." + 4 chars (per crud.get_user_api_token)
        self.assertRegex(masked, r'^[A-Za-z0-9_-]{8}\.\.\.[A-Za-z0-9_-]{4}$',
                         f"masked token must match 'XXXXXXXX...YYYY' format: {masked!r}")
        # Raw token must NOT appear in masked form
        self.assertNotIn(raw, masked,
                         f"raw token leaked via GET: raw={raw!r} masked={masked!r}")

    async def test_get_tokens_unauthenticated_returns_401(self):
        r = await self.client.get('/api/v1/tokens')
        self.assertEqual(r.status_code, 401, r.text)

    # ─────────────────────────── POST /api/v1/tokens ───────────────────────────

    async def test_create_token_returns_raw_once(self):
        """POST returns the raw token (only chance to copy it)."""
        token = await self._login()
        r = await self.client.post(
            '/api/v1/tokens',
            headers={'Authorization': f'Bearer {token}'},
        )
        self.assertEqual(r.status_code, 201, r.text)
        body = r.json()
        self.assertIn('token', body)
        raw = body['token']
        self.assertIsInstance(raw, str)
        self.assertGreater(len(raw), 30)

    async def test_create_token_twice_returns_different_tokens(self):
        """Two POSTs without a rotate call between them: the create_user_api_token
        function doesn't delete the old one — it inserts a new row. The CRUD
        layer's get_user_api_token selects the first one. The Astro /tokens page
        should rotate, not create twice. We pin the actual behaviour: second
        POST returns a new raw token."""
        token = await self._login()
        r1 = await self.client.post(
            '/api/v1/tokens',
            headers={'Authorization': f'Bearer {token}'},
        )
        r2 = await self.client.post(
            '/api/v1/tokens',
            headers={'Authorization': f'Bearer {token}'},
        )
        self.assertEqual(r1.status_code, 201, r1.text)
        self.assertEqual(r2.status_code, 201, r2.text)
        self.assertNotEqual(r1.json()['token'], r2.json()['token'])

    async def test_create_token_unauthenticated_returns_401(self):
        r = await self.client.post('/api/v1/tokens')
        self.assertEqual(r.status_code, 401, r.text)

    # ─────────────────────────── POST /api/v1/tokens/rotate ───────────────────────────

    async def test_rotate_token_returns_new_raw_and_invalidates_old(self):
        """Rotate returns a new raw token. The old token, if it had been used
        as a Bearer, would now fail to authenticate."""
        token_jwt = await self._login()
        # Create initial
        create = await self.client.post(
            '/api/v1/tokens',
            headers={'Authorization': f'Bearer {token_jwt}'},
        )
        old_raw = create.json()['token']

        # Rotate
        rotate = await self.client.post(
            '/api/v1/tokens/rotate',
            headers={'Authorization': f'Bearer {token_jwt}'},
        )
        self.assertEqual(rotate.status_code, 200, rotate.text)
        new_raw = rotate.json()['token']
        self.assertNotEqual(old_raw, new_raw)

        # The old raw token must no longer authenticate. Verify via the
        # protected endpoint: /api/v1/habits with Bearer old_raw → 401.
        r = await self.client.get(
            '/api/v1/habits',
            headers={'Authorization': f'Bearer {old_raw}'},
        )
        self.assertEqual(r.status_code, 401, r.text)

        # The new raw token must work.
        r2 = await self.client.get(
            '/api/v1/habits',
            headers={'Authorization': f'Bearer {new_raw}'},
        )
        self.assertEqual(r2.status_code, 200, r2.text)

    async def test_rotate_without_existing_token_creates_new(self):
        """Rotating without a prior token creates one. Pin this so we know
        what the /tokens page does when the user clicks 'Rotate' for the
        first time."""
        token = await self._login()
        r = await self.client.post(
            '/api/v1/tokens/rotate',
            headers={'Authorization': f'Bearer {token}'},
        )
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn('token', r.json())

    async def test_rotate_unauthenticated_returns_401(self):
        r = await self.client.post('/api/v1/tokens/rotate')
        self.assertEqual(r.status_code, 401, r.text)

    # ─────────────────────────── DELETE /api/v1/tokens ───────────────────────────

    async def test_delete_token_returns_204_and_invalidates(self):
        """DELETE returns 204 with no body. The previously-valid token
        must now 401 on the next protected call."""
        token_jwt = await self._login()
        create = await self.client.post(
            '/api/v1/tokens',
            headers={'Authorization': f'Bearer {token_jwt}'},
        )
        raw = create.json()['token']

        # Verify it works
        r0 = await self.client.get(
            '/api/v1/habits',
            headers={'Authorization': f'Bearer {raw}'},
        )
        self.assertEqual(r0.status_code, 200, r0.text)

        # Revoke
        r = await self.client.delete(
            '/api/v1/tokens',
            headers={'Authorization': f'Bearer {token_jwt}'},
        )
        self.assertEqual(r.status_code, 204, r.text)
        self.assertEqual(r.text, '')

        # Old token now invalid
        r2 = await self.client.get(
            '/api/v1/habits',
            headers={'Authorization': f'Bearer {raw}'},
        )
        self.assertEqual(r2.status_code, 401, r2.text)

        # GET returns null (no token row)
        r3 = await self.client.get(
            '/api/v1/tokens',
            headers={'Authorization': f'Bearer {token_jwt}'},
        )
        self.assertEqual(r3.json(), {'token': None})

    async def test_delete_token_when_none_exists_is_idempotent(self):
        """Deleting when no token exists should not 500."""
        token = await self._login()
        r = await self.client.delete(
            '/api/v1/tokens',
            headers={'Authorization': f'Bearer {token}'},
        )
        self.assertEqual(r.status_code, 204, r.text)

    async def test_delete_token_unauthenticated_returns_401(self):
        r = await self.client.delete('/api/v1/tokens')
        self.assertEqual(r.status_code, 401, r.text)

    # ─────────────────────────── Cross-user isolation ───────────────────────────

    async def test_token_rotate_does_not_affect_other_users(self):
        """User A's rotate must not invalidate User B's token."""
        token_a = await self._login()
        # Create B
        manager_b = UserManager(SQLAlchemyUserDatabase(db.async_session_maker(), db.User))
        user_b = await manager_b.create(
            UserCreate(email='b@example.com', password=PASSWORD)
        )
        await views.get_or_create_user_habit_list(user_b, views.dummy_empty_habit_list())
        token_b = (await self.client.post(
            '/auth/login',
            data={'username': user_b.email, 'password': PASSWORD},
        )).json()['access_token']

        # Both create tokens
        a_raw = (await self.client.post(
            '/api/v1/tokens',
            headers={'Authorization': f'Bearer {token_a}'},
        )).json()['token']
        b_raw = (await self.client.post(
            '/api/v1/tokens',
            headers={'Authorization': f'Bearer {token_b}'},
        )).json()['token']

        # A rotates
        await self.client.post(
            '/api/v1/tokens/rotate',
            headers={'Authorization': f'Bearer {token_a}'},
        )

        # B's token still works
        r = await self.client.get(
            '/api/v1/habits',
            headers={'Authorization': f'Bearer {b_raw}'},
        )
        self.assertEqual(r.status_code, 200, r.text)


class Slice7OtherPagesTests(unittest.IsolatedAsyncioTestCase):
    """Verification that the other Slice 7 pages have backend contracts.

    This class doesn't add backend tests — it documents that the
    surface for /help, /settings, and /account/delete is already pinned
    by earlier slices. The intent: a future reviewer reading the slice
    report can see at a glance which contracts are covered.
    """

    async def asyncSetUp(self):
        async with db.engine.begin() as conn:
            await conn.run_sync(db.Base.metadata.drop_all)
        await db.create_db_and_tables()
        reset_routes._rate_limit_cache.clear()
        session = db.async_session_maker()
        manager = UserManager(SQLAlchemyUserDatabase(session, db.User))
        self.user = await manager.create(
            UserCreate(email='other@example.com', password=PASSWORD)
        )
        await views.get_or_create_user_habit_list(self.user, views.dummy_empty_habit_list())
        self.session = session
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test')

    async def asyncTearDown(self):
        await self.client.aclose()
        await self.session.close()
        await db.engine.dispose()

    async def test_help_page_is_static_no_backend(self):
        """The /help page renders Markdown content from the Astro project.
        No backend endpoint. Verify by asserting that there's no /api/v1/help
        endpoint (so any future implementation knows there's nothing to call)."""
        token = (await self.client.post(
            '/auth/login',
            data={'username': self.user.email, 'password': PASSWORD},
        )).json()['access_token']
        r = await self.client.get(
            '/api/v1/help',
            headers={'Authorization': f'Bearer {token}'},
        )
        # 404 (route doesn't exist) — this is the contract we want.
        self.assertEqual(r.status_code, 404, r.text)

    async def test_settings_has_no_specific_backend_endpoint(self):
        """The /settings page is mostly client-side (theme + custom CSS).
        Custom CSS is stored client-side only in the migration branch
        (parity row 4.2 says backend endpoint is TODO)."""
        # We just verify there's no /api/v1/settings endpoint — any
        # future implementation that adds one will need a slice and tests.
        r = await self.client.get('/api/v1/settings')
        self.assertEqual(r.status_code, 404, r.text)


if __name__ == '__main__':
    unittest.main()
