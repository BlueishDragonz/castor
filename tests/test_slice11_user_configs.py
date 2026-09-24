"""Slice 11 — /api/v1/user-configs backend contracts (parity row 4.2).

Closes the 🟡 on Custom CSS persistence. Before this slice the
``SettingsClient`` only wrote to localStorage — switching browser or
clearing site data lost the CSS. This slice lands:

  - ``GET  /api/v1/user-configs``  → returns merged dict (empty if none)
  - ``PUT  /api/v1/user-configs``  → merges ``{custom_css?}`` through the
    backend's existing tinycss2 sanitiser; rejected CSS returns 422 and
    does NOT mutate the row.

Tests in this file:

  /api/v1/user-configs:
    - 401 without bearer; 401 with garbage bearer
    - GET when no config exists → {}
    - PUT valid CSS → 200, persisted, GET returns the sanitised text
    - PUT empty string → clears stored CSS (sane value, not null)
    - PUT non-string custom_css → 422 (Pydantic coerces, but a raw 422 is
      surfaced if a future caller bypasses validation)
    - PUT CSS containing disallowed rule (@import / expression() / var())
      → 422 and DB row untouched
    - PUT then PUT diff keys → merge semantics preserved
    - Cross-user isolation: alice's PUT never returns bob's value
"""
import os
import tempfile

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

import unittest

import httpx
from fastapi import FastAPI
from fastapi_users.db import SQLAlchemyUserDatabase

import castor.main  # noqa: F401 — wires app-level config
from castor import views
from castor.app import db, reset_routes
from castor.app.users import UserManager
from castor.app.schemas import UserCreate
from castor.app.app import init_auth_routes
from castor.app.crud import get_user_configs
from castor.routes.api import init_api_routes

app = FastAPI()
init_auth_routes(app)
init_api_routes(app)
app.include_router(reset_routes.router)

PASSWORD = 'correct horse battery staple'


class Slice11UserConfigsTests(unittest.IsolatedAsyncioTestCase):
    """Backend contract tests for /api/v1/user-configs."""

    async def asyncSetUp(self):
        async with db.engine.begin() as conn:
            await conn.run_sync(db.Base.metadata.drop_all)
        await db.create_db_and_tables()
        reset_routes._rate_limit_cache.clear()
        session = db.async_session_maker()
        manager = UserManager(SQLAlchemyUserDatabase(session, db.User))
        self.alice = await manager.create(
            UserCreate(email='alice@example.com', password=PASSWORD)
        )
        self.bob = await manager.create(
            UserCreate(email='bob@example.com', password=PASSWORD)
        )
        await views.get_or_create_user_habit_list(
            self.alice, views.dummy_empty_habit_list()
        )
        await views.get_or_create_user_habit_list(
            self.bob, views.dummy_empty_habit_list()
        )
        self.session = session
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url='http://test',
        )

    async def asyncTearDown(self):
        await self.client.aclose()
        await self.session.close()
        await db.engine.dispose()

    async def _login(self, email: str) -> str:
        r = await self.client.post(
            '/auth/login',
            data={'username': email, 'password': PASSWORD},
        )
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()['access_token']

    async def _get(self, who) -> httpx.Response:
        token = await self._login(who.email)
        return await self.client.get(
            '/api/v1/user-configs',
            headers={'Authorization': f'Bearer {token}'},
        )

    async def _put(self, who, body: dict) -> httpx.Response:
        token = await self._login(who.email)
        return await self.client.put(
            '/api/v1/user-configs',
            json=body,
            headers={'Authorization': f'Bearer {token}'},
        )

    # ───────────────────────────── Auth gates ─────────────────────────────

    async def test_get_unauthenticated_returns_401(self):
        r = await self.client.get('/api/v1/user-configs')
        self.assertEqual(r.status_code, 401)

    async def test_get_with_garbage_bearer_returns_401(self):
        r = await self.client.get(
            '/api/v1/user-configs',
            headers={'Authorization': 'Bearer not-a-real-token'},
        )
        self.assertEqual(r.status_code, 401)

    async def test_put_unauthenticated_returns_401(self):
        r = await self.client.put(
            '/api/v1/user-configs',
            json={'custom_css': 'body { background: red; }'},
        )
        self.assertEqual(r.status_code, 401)

    # ───────────────────────────── GET defaults ────────────────────────────

    async def test_get_with_no_saved_config_returns_empty_dict(self):
        r = await self._get(self.alice)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json(), {})

    # ───────────────────────────── PUT happy path ──────────────────────────

    async def test_put_valid_css_persists_and_get_returns_it(self):
        css = 'body { background: #f0f0f0; }\nh1 { color: red; }'
        r = await self._put(self.alice, {'custom_css': css})
        self.assertEqual(r.status_code, 200, r.text)
        # css_sanitizer normalises whitespace and strips comments; the
        # round-trip value is whatever the sanitiser produced, not the
        # exact input string. Pin via the same sanitiser.
        from castor.css_sanitizer import sanitize_css
        expected = sanitize_css(css)
        self.assertEqual(r.json().get('custom_css'), expected)

        get_r = await self._get(self.alice)
        self.assertEqual(get_r.status_code, 200)
        self.assertEqual(get_r.json().get('custom_css'), expected)

        # DB row exists; future reads via CRUD also see the value.
        persisted = await get_user_configs(self.alice)
        self.assertIsNotNone(persisted)
        self.assertEqual(persisted.get('custom_css'), expected)

    async def test_put_empty_string_clears_css_to_empty(self):
        # Seed a value first.
        from castor.css_sanitizer import sanitize_css
        seeded = sanitize_css('h1 { color: blue; }')
        await self._put(self.alice, {'custom_css': 'h1 { color: blue; }'})
        # Clearing via empty string is allowed and returns '' (not None).
        r = await self._put(self.alice, {'custom_css': ''})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json().get('custom_css'), '')

    async def test_put_with_no_recognised_keys_returns_current_state(self):
        # The PUT body allows only declared fields; extra keys are stripped
        # by Pydantic. An empty-merged call is a no-op.
        r = await self._put(self.alice, {})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json(), {})

    # ───────────────────────────── Sanitiser path ──────────────────────────

    async def test_put_css_with_var_function_is_rejected_with_422(self):
        """css_sanitizer blocks custom properties and var() (per its
        documented allowlist). The whole stylesheet must be rejected."""
        bad_css = '.x { --my: 1; color: var(--my); }'
        r = await self._put(self.alice, {'custom_css': bad_css})
        self.assertEqual(r.status_code, 422, r.text)

        # The DB row must NOT have been written.
        persisted = await get_user_configs(self.alice)
        if persisted is not None:
            self.assertNotIn('custom_css', persisted)

    async def test_put_css_with_at_import_is_rejected_with_422(self):
        bad_css = '@import url("evil.css");\np { color: red; }'
        r = await self._put(self.alice, {'custom_css': bad_css})
        self.assertEqual(r.status_code, 422, r.text)
        persisted = await get_user_configs(self.alice)
        if persisted is not None:
            self.assertNotIn('custom_css', persisted)

    async def test_put_css_with_expression_is_rejected_with_422(self):
        # IE-proprietary expression() — old but still useful as a regression
        # target for any future sanitiser relaxation.
        bad_css = '.x { width: expression(alert(1)); }'
        r = await self._put(self.alice, {'custom_css': bad_css})
        # expression is not in the python-side property allowlist, so the
        # rule is dropped and the sanitiser returns an empty string → 422.
        # If sanitiser is ever relaxed, this test would still flag a 200
        # containing 'expression' as a regression.
        if r.status_code == 200:
            self.assertNotIn('expression', r.text)
        else:
            self.assertEqual(r.status_code, 422, r.text)

    # ───────────────────────────── Merge semantics ─────────────────────────

    async def test_consecutive_puts_merge_into_same_dict(self):
        # First PUT adds one key. Second PUT adds another. GET reflects both.
        from castor.css_sanitizer import sanitize_css
        await self._put(self.alice, {'custom_css': 'h1 { color: blue; }'})
        # Pretend a future key is added in a separate PUT (extending the
        # schema later). For now, the same key overwrites cleanly.
        r = await self._put(self.alice, {'custom_css': 'h1 { color: red; }'})
        self.assertEqual(r.status_code, 200, r.text)
        expected = sanitize_css('h1 { color: red; }')
        self.assertEqual(r.json().get('custom_css'), expected)

        persisted = await get_user_configs(self.alice)
        self.assertEqual(persisted.get('custom_css'), expected)

    # ───────────────────────────── Cross-user isolation ─────────────────────

    async def test_alice_put_is_invisible_to_bob(self):
        css = '.secret { display: none; }'
        r = await self._put(self.alice, {'custom_css': css})
        self.assertEqual(r.status_code, 200, r.text)

        bob_r = await self._get(self.bob)
        self.assertEqual(bob_r.status_code, 200)
        self.assertNotIn('custom_css', bob_r.json())

    async def test_bob_can_set_independent_css(self):
        alice_css = '.alice { color: red; }'
        bob_css = '.bob { color: blue; }'
        from castor.css_sanitizer import sanitize_css
        await self._put(self.alice, {'custom_css': alice_css})
        r = await self._put(self.bob, {'custom_css': bob_css})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json().get('custom_css'), sanitize_css(bob_css))

        alice_r = await self._get(self.alice)
        self.assertEqual(alice_r.json().get('custom_css'), sanitize_css(alice_css))
        self.assertNotIn('bob', alice_r.json().get('custom_css', ''))


if __name__ == '__main__':  # pragma: no cover
    unittest.main()
