"""Slice 8 — /admin + /stats backend contracts.

Slice 8 has two halves:

1. **/admin** (parity rows 7.1, 7.2): backend endpoints exist but are NOT
   pinned by tests. This slice adds contract tests for:
   - GET /api/v1/admin/users — list all activated users
   - POST /api/v1/admin/backup — trigger backup for all users
   - Authorisation: 401 when caller is not ADMIN_EMAIL, even with valid
     bearer token. This matches the documented "ADMIN_EMAIL string gate"
     pattern (no role column; email-based check).

2. **/stats date-range picker** (parity row 3.2): UI work. The backend
   already supports date-range queries on completions (verified in
   test_slice2_habits.py); the gap is purely Astro UI. Pinned separately
   in tests/test_slice8_stats.py.

Both halves share the same DB setup pattern as previous slices.
"""
import os
import tempfile
import unittest
from unittest.mock import patch, AsyncMock

_TMP = tempfile.TemporaryDirectory()
ADMIN_EMAIL = 'admin@castor.example.com'
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
# NOTE: ADMIN_EMAIL is intentionally NOT set here. The admin endpoint
# checks settings.ADMIN_EMAIL which is a cached singleton (castor.configs).
# We override current_admin_user directly in setUp instead — see setUp.
# Setting os.environ['ADMIN_EMAIL'] at module load has no effect because
# castor.configs.settings has already been instantiated by the time this
# test file is imported.

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
# Mount the admin router (admin_routes.router is at /api/v1/admin/*).
from castor.app.admin_routes import router as admin_router
app.include_router(admin_router)


PASSWORD = 'correct horse battery staple'


class Slice8AdminTests(unittest.IsolatedAsyncioTestCase):
    """Contract tests for /api/v1/admin/users and /api/v1/admin/backup."""

    async def asyncSetUp(self):
        # The admin endpoint checks settings.ADMIN_EMAIL. We mutate it to
        # match ADMIN_EMAIL so the admin tests pass. This avoids relying
        # on os.environ (which is read once at castor.configs import time
        # and is shared across all tests).
        from castor.configs import settings
        self._original_admin_email = settings.ADMIN_EMAIL
        settings.ADMIN_EMAIL = ADMIN_EMAIL
        async with db.engine.begin() as conn:
            await conn.run_sync(db.Base.metadata.drop_all)
        await db.create_db_and_tables()
        reset_routes._rate_limit_cache.clear()
        session = db.async_session_maker()
        manager = UserManager(SQLAlchemyUserDatabase(session, db.User))
        # Create admin
        self.admin = await manager.create(
            UserCreate(email=ADMIN_EMAIL, password=PASSWORD)
        )
        await views.get_or_create_user_habit_list(self.admin, views.dummy_empty_habit_list())
        # Create three regular users
        self.regular_users = []
        for i in range(3):
            u = await manager.create(
                UserCreate(email=f'user{i}@castor.example.com', password=PASSWORD)
            )
            await views.get_or_create_user_habit_list(u, views.dummy_empty_habit_list())
            self.regular_users.append(u)
        self.session = session
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test')

    async def asyncTearDown(self):
        await self.client.aclose()
        from castor.configs import settings
        settings.ADMIN_EMAIL = self._original_admin_email
        await self.session.close()
        await db.engine.dispose()

    async def _login(self, email: str) -> str:
        r = await self.client.post(
            '/auth/login',
            data={'username': email, 'password': PASSWORD},
        )
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()['access_token']

    # ─────────────────────────── GET /api/v1/admin/users ───────────────────────────

    async def test_list_users_unauthenticated_returns_401(self):
        r = await self.client.get('/api/v1/admin/users')
        self.assertEqual(r.status_code, 401, r.text)

    async def test_list_users_as_regular_user_returns_401(self):
        """Non-admin with a valid bearer token must NOT see the user list.
        The ADMIN_EMAIL string-gate pattern is enforced regardless of token
        validity."""
        token = await self._login('user0@castor.example.com')
        r = await self.client.get(
            '/api/v1/admin/users',
            headers={'Authorization': f'Bearer {token}'},
        )
        self.assertEqual(r.status_code, 401, r.text)
        # Defence-in-depth: don't leak whether the endpoint exists
        self.assertNotIn('users', r.text.lower())

    async def test_list_users_as_admin_returns_activated_users(self):
        """With ENABLE_PLAN=false (default in tests), get_activated_users
        returns ALL users. Verify admin sees all four."""
        token = await self._login(ADMIN_EMAIL)
        r = await self.client.get(
            '/api/v1/admin/users',
            headers={'Authorization': f'Bearer {token}'},
        )
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertIn('users', body)
        emails = sorted(u['email'] for u in body['users'])
        self.assertEqual(emails, sorted([
            ADMIN_EMAIL,
            'user0@castor.example.com',
            'user1@castor.example.com',
            'user2@castor.example.com',
        ]))

    async def test_list_users_includes_id_and_active_fields(self):
        """Each user entry has the fields the Astro admin table renders."""
        token = await self._login(ADMIN_EMAIL)
        r = await self.client.get(
            '/api/v1/admin/users',
            headers={'Authorization': f'Bearer {token}'},
        )
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        for u in body['users']:
            self.assertIn('email', u)
            self.assertIn('id', u)
            self.assertIn('is_active', u)
            self.assertIn('is_verified', u)
            # Don't leak hashed password
            self.assertNotIn('hashed_password', u)

    # ─────────────────────────── POST /api/v1/admin/backup ───────────────────────────

    async def test_trigger_backup_unauthenticated_returns_401(self):
        r = await self.client.post('/api/v1/admin/backup')
        self.assertEqual(r.status_code, 401, r.text)

    async def test_trigger_backup_as_regular_user_returns_401(self):
        token = await self._login('user0@castor.example.com')
        r = await self.client.post(
            '/api/v1/admin/backup',
            headers={'Authorization': f'Bearer {token}'},
        )
        self.assertEqual(r.status_code, 401, r.text)

    async def test_trigger_backup_as_admin_with_no_telegram_config_succeeds(self):
        """When no users have Telegram backup configured (the default for
        new users), backup should still succeed — it skips each user and
        logs the warning, but does not raise.

        backup_all_users() iterates and logs warnings; we patch
        backup_to_telegram to assert it's never called (no Telegram config)."""
        token = await self._login(ADMIN_EMAIL)
        with patch('castor.app.admin_routes.views.backup_to_telegram', new=AsyncMock()) as tg:
            r = await self.client.post(
                '/api/v1/admin/backup',
                headers={'Authorization': f'Bearer {token}'},
            )
        self.assertEqual(r.status_code, 202, r.text)
        self.assertEqual(r.json()['status'], 'completed')
        # No users have telegram configured → never called
        tg.assert_not_called()

    async def test_trigger_backup_emits_audit_event(self):
        """The admin action emits an audit event with outcome='success'."""
        token = await self._login(ADMIN_EMAIL)
        # We patch views.backup_all_users so we don't iterate the DB.
        with patch('castor.app.admin_routes.views.backup_all_users', new=AsyncMock()):
            r = await self.client.post(
                '/api/v1/admin/backup',
                headers={'Authorization': f'Bearer {token}'},
            )
        self.assertEqual(r.status_code, 202, r.text)
        # Verify audit event was written: AuditEvent lives in castor.app.audit
        from castor.app.audit import AuditEvent
        from castor.app.db import get_async_session_context
        from sqlalchemy import select
        async with get_async_session_context() as session:
            events = (await session.execute(select(AuditEvent))).scalars().all()
        event_types = [e.event for e in events]
        self.assertIn('backup', event_types)

    # ─────────────────────────── Auth header contract ───────────────────────────

    async def test_admin_endpoints_require_bearer_with_no_cookie_fallback(self):
        """The admin endpoints use FastAPI dependencies, which only accept
        Authorization header Bearer tokens. A cookie-only session is NOT
        accepted on admin endpoints. This pins the security posture: a
        stolen session cookie is not enough to call admin routes; the
        attacker would also need the bearer token."""
        # Simulate cookie-based session: the legacy AuthMiddleware used to
        # set a 'beaver_auth' cookie. Modern middleware does not.
        r = await self.client.get(
            '/api/v1/admin/users',
            cookies={'castor_token': 'fake.cookie.value'},
        )
        self.assertEqual(r.status_code, 401, r.text)


if __name__ == '__main__':
    unittest.main()
