"""Slice 12b — Display preferences (parity row 12.6).

Extends the /api/v1/user-configs surface with three display-preference
booleans:

  - show_streak    (parity 12.6: "show streak badge")
  - show_total     (parity 12.6: "show total badge")
  - date_reverse   (parity 12.6: "date columns reversed")

These were previously read on /habits/index.astro from cookies but
nothing on the migrated site wrote them — true persistence gap.

This file covers the BACKEND contract:
  - PUT boolean → 200, persisted, GET reflects
  - PUT non-boolean → 422
  - PUT multiple keys → merge semantics
  - Cross-user isolation
  - All three keys coexist with `custom_css` without clobbering each other
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

import castor.main  # noqa: F401
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


class Slice12bDisplayPrefsTests(unittest.IsolatedAsyncioTestCase):
    """Backend contract tests for /api/v1/user-configs display-pref keys."""

    async def asyncSetUp(self):
        async with db.engine.begin() as conn:
            await conn.run_sync(db.Base.metadata.drop_all)
        await db.create_db_and_tables()
        reset_routes._rate_limit_cache.clear()
        session = db.async_session_maker()
        manager = UserManager(SQLAlchemyUserDatabase(session, db.User))
        self.alice = await manager.create(
            UserCreate(email='alice-display@example.com', password=PASSWORD)
        )
        self.bob = await manager.create(
            UserCreate(email='bob-display@example.com', password=PASSWORD)
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

    async def _put(self, who, body: dict) -> httpx.Response:
        token = await self._login(who.email)
        return await self.client.put(
            '/api/v1/user-configs',
            json=body,
            headers={'Authorization': f'Bearer {token}'},
        )

    async def _get(self, who) -> httpx.Response:
        token = await self._login(who.email)
        return await self.client.get(
            '/api/v1/user-configs',
            headers={'Authorization': f'Bearer {token}'},
        )

    # ───────────────────────────── Defaults ──────────────────────────────

    async def test_get_no_saved_configs_has_no_display_keys(self):
        r = await self._get(self.alice)
        self.assertEqual(r.status_code, 200)
        body = r.json()
        for key in ('show_streak', 'show_total', 'date_reverse'):
            self.assertNotIn(key, body, f'fresh user should have no {key}')

    # ───────────────────────────── Boolean PUTs ──────────────────────────

    async def test_put_show_streak_true_persists(self):
        r = await self._put(self.alice, {'show_streak': True})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json().get('show_streak'), True)

        get_r = await self._get(self.alice)
        self.assertEqual(get_r.json().get('show_streak'), True)

        # Direct CRUD read should agree.
        persisted = await get_user_configs(self.alice)
        self.assertEqual(persisted.get('show_streak'), True)

    async def test_put_show_total_false_explicitly_persists(self):
        # Explicit false (not just "missing") still overwrites the row.
        r = await self._put(self.alice, {'show_total': False})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json().get('show_total'), False)

        get_r = await self._get(self.alice)
        self.assertEqual(get_r.json().get('show_total'), False)

    async def test_put_date_reverse_toggles_cleanly(self):
        # Toggle on
        r = await self._put(self.alice, {'date_reverse': True})
        self.assertEqual(r.json().get('date_reverse'), True)
        # Toggle off (replaces, not merges oddly)
        r = await self._put(self.alice, {'date_reverse': False})
        self.assertEqual(r.json().get('date_reverse'), False)

    # ───────────────────────────── Multi-key merge ───────────────────────

    async def test_put_all_three_booleans_at_once(self):
        r = await self._put(self.alice, {
            'show_streak': True,
            'show_total': True,
            'date_reverse': True,
        })
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body.get('show_streak'), True)
        self.assertEqual(body.get('show_total'), True)
        self.assertEqual(body.get('date_reverse'), True)

    async def test_put_coexists_with_custom_css(self):
        # CSS first, then a boolean — neither should clobber the other.
        from castor.css_sanitizer import sanitize_css
        css = 'h1 { color: red; }'
        await self._put(self.alice, {'custom_css': css})
        r = await self._put(self.alice, {'show_streak': True})
        body = r.json()
        self.assertEqual(body.get('custom_css'), sanitize_css(css))
        self.assertEqual(body.get('show_streak'), True)

        # And the other way around.
        r = await self._put(self.alice, {'show_total': True})
        body = r.json()
        self.assertEqual(body.get('custom_css'), sanitize_css(css))
        self.assertEqual(body.get('show_streak'), True)
        self.assertEqual(body.get('show_total'), True)

    # ───────────────────────────── Type validation ───────────────────────

    async def test_put_string_instead_of_boolean_returns_422(self):
        r = await self._put(self.alice, {'show_streak': 'yes'})
        self.assertEqual(r.status_code, 422, r.text)
        # 422 shouldn't have written anything.
        get_r = await self._get(self.alice)
        self.assertNotIn('show_streak', get_r.json())

    async def test_put_int_instead_of_boolean_returns_422(self):
        # Pydantic coerces 0/1 to bools in lax mode; we want strict False.
        # Castor's Pydantic model is the strict-bool default — verify.
        r = await self._put(self.alice, {'show_total': 1})
        self.assertEqual(r.status_code, 422, r.text)

    # ───────────────────────────── Cross-user isolation ──────────────────

    async def test_alice_boolean_does_not_leak_to_bob(self):
        await self._put(self.alice, {'show_streak': True})
        bob_r = await self._get(self.bob)
        self.assertEqual(bob_r.status_code, 200)
        self.assertNotIn('show_streak', bob_r.json())

    async def test_alice_can_flip_independently_of_bob(self):
        await self._put(self.alice, {'show_streak': True})
        # Bob has not set anything yet → missing key.
        bob_r = await self._get(self.bob)
        self.assertNotIn('show_streak', bob_r.json())

        await self._put(self.bob, {'show_streak': False})
        alice_r = await self._get(self.alice)
        bob_r2 = await self._get(self.bob)
        self.assertEqual(alice_r.json().get('show_streak'), True)
        self.assertEqual(bob_r2.json().get('show_streak'), False)


if __name__ == '__main__':  # pragma: no cover
    unittest.main()
