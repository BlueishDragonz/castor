"""Priority 1 behavioral tests. Uses a disposable SQLite DB, never live data.
Run: python -m unittest discover -s tests -p test_priority1_auth.py -v
"""
import os
import tempfile
import unittest
import uuid
import datetime
from unittest.mock import AsyncMock, patch

_TMP = tempfile.TemporaryDirectory()
os.environ['DATABASE_URL'] = f'sqlite+aiosqlite:///{_TMP.name}/test.db'
os.environ['JWT_SECRET'] = 'test-only-jwt-secret-not-for-production'
os.environ['RESET_PASSWORD_TOKEN_SECRET'] = 'test-only-reset-secret'
os.environ['NICEGUI_STORAGE_SECRET'] = 'test-only-storage-secret'
os.environ['JWT_LIFETIME_SECONDS'] = '3600'
os.environ['REQUIRE_ADMIN_FOR_REGISTRATION'] = 'false'
os.environ['TRUSTED_LOCAL_EMAIL'] = ''
os.environ['TRUSTED_EMAIL_HEADER'] = ''

import httpx
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import create_async_engine
from fastapi import FastAPI
from fastapi_users import exceptions
from fastapi_users.db import SQLAlchemyUserDatabase
from fastapi_users.jwt import generate_jwt, decode_jwt
import beaverhabits.main  # initialize the application's circular import graph
from beaverhabits.app import db, auth, reset_routes
from beaverhabits.app.users import UserManager, get_jwt_strategy
from beaverhabits.app.schemas import UserCreate
from beaverhabits.app.app import init_auth_routes

app = FastAPI()
init_auth_routes(app)
app.include_router(reset_routes.router)
PASSWORD = 'original passphrase'
NEW_PASSWORD = 'replacement passphrase'


class AuthTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        async with db.engine.begin() as conn:
            await conn.run_sync(db.Base.metadata.drop_all)
        await db.create_db_and_tables()
        reset_routes._rate_limit_cache.clear()
        self.session = db.async_session_maker()
        self.manager = UserManager(SQLAlchemyUserDatabase(self.session, db.User))
        self.user = await self.manager.create(UserCreate(email='fixture@example.com', password=PASSWORD))
        self.strategy = get_jwt_strategy()
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test')

    async def asyncTearDown(self):
        await self.client.aclose()
        await self.session.close()
        await db.engine.dispose()

    async def login(self, password=PASSWORD):
        r = await self.client.post('/auth/login', data={'username': self.user.email, 'password': password})
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()['access_token']

    async def test_register_rejects_short_password(self):
        r = await self.client.post('/auth/register', json={'email': 'new@example.com', 'password': 'elevenchars'})
        self.assertEqual(r.status_code, 400, r.text)
        self.assertIn('12', r.text)

    async def test_register_accepts_twelve_characters(self):
        r = await self.client.post('/auth/register', json={'email': 'new@example.com', 'password': '123456789012'})
        self.assertEqual(r.status_code, 201, r.text)
        self.assertNotIn('token_version', r.json())

    async def test_change_rejects_short_password_without_revoking(self):
        token = await self.login()
        r = await self.client.post('/auth/webauthn/change-password', headers={'Authorization': f'Bearer {token}'}, json={'current_password': PASSWORD, 'new_password': 'short'})
        self.assertEqual(r.status_code, 400, r.text)
        self.assertEqual((await self.client.get('/users/me', headers={'Authorization': f'Bearer {token}'})).status_code, 200)
        await self.login()

    async def test_change_revokes_old_token_and_new_login_works(self):
        token = await self.login()
        r = await self.client.post('/auth/webauthn/change-password', headers={'Authorization': f'Bearer {token}'}, json={'current_password': PASSWORD, 'new_password': NEW_PASSWORD})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual((await self.client.get('/users/me', headers={'Authorization': f'Bearer {token}'})).status_code, 401)
        self.assertIsNone(await auth.user_from_token(token))
        self.assertFalse(await auth.user_check_token(token))
        fresh = await self.login(NEW_PASSWORD)
        self.assertIsNotNone(await auth.user_from_token(fresh))
        old = await self.client.post('/auth/login', data={'username': self.user.email, 'password': PASSWORD})
        self.assertEqual(old.status_code, 400)

    async def test_sensitive_endpoints_require_current_password(self):
        token = await self.login()
        headers = {'Authorization': f'Bearer {token}'}
        response = await self.client.post('/auth/webauthn/change-password', headers=headers,
                                          json={'new_password': NEW_PASSWORD})
        self.assertEqual(response.status_code, 422)
        response = await self.client.request('DELETE', '/auth/webauthn/credentials/Zml4dHVyZQ', headers=headers)
        self.assertEqual(response.status_code, 422)
        self.assertEqual((await self.client.patch('/users/me', headers=headers,
                          json={'email': 'replacement@example.com'})).status_code, 422)
        await self.login()

    async def test_direct_gui_change_uses_policy(self):
        with self.assertRaises(exceptions.InvalidPasswordException):
            await auth.user_reset_password(await auth.user_get_by_id(self.user.id), 'short')

    async def test_direct_gui_change_revokes(self):
        token = await self.strategy.write_token(self.user)
        await auth.user_reset_password(await auth.user_get_by_id(self.user.id), NEW_PASSWORD)
        self.assertFalse(await auth.user_check_token(token))

    async def test_legacy_jwt_without_version_rejected(self):
        token = generate_jwt({'sub': str(self.user.id), 'aud': self.strategy.token_audience}, self.strategy.encode_key, 3600)
        self.assertIsNone(await self.strategy.read_token(token, self.manager))

    async def test_invalid_version_types_rejected(self):
        for value in [None, False, '0', -1]:
            token = generate_jwt({'sub': str(self.user.id), 'aud': self.strategy.token_audience, 'ver': value}, self.strategy.encode_key, 3600)
            self.assertIsNone(await self.strategy.read_token(token, self.manager))

    async def test_non_password_update_preserves_session(self):
        token = await self.login()
        await self.manager._update(self.user, {'is_verified': True})
        self.assertIsNotNone(await auth.user_from_token(token))

    async def test_two_stale_sessions_increment_atomically(self):
        async with db.async_session_maker() as second:
            stale = await second.get(db.User, self.user.id)
            await self.manager._update(self.user, {'password': NEW_PASSWORD})
            interim = await self.strategy.write_token(self.user)
            other = UserManager(SQLAlchemyUserDatabase(second, db.User))
            updated = await other._update(stale, {'password': 'third valid passphrase'})
            self.assertEqual(updated.token_version, 2)
            self.assertIsNone(await self.strategy.read_token(interim, self.manager))

    async def make_reset(self):
        code = '123456789012'
        self.session.add(db.PasswordResetCode(email=self.user.email, user_id=self.user.id, token_version=self.user.token_version, code_hash=reset_routes._hash_code(code), expires_at=datetime.datetime.now(datetime.timezone.utc)+datetime.timedelta(minutes=5)))
        await self.session.commit()
        return code

    async def test_code_reset_revokes_and_cannot_replay(self):
        token = await self.login()
        code = await self.make_reset()
        body = {'email': self.user.email, 'code': code, 'new_password': NEW_PASSWORD}
        r = await self.client.post('/auth/reset-password', json=body)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertFalse(await auth.user_check_token(token))
        self.assertEqual((await self.client.post('/auth/reset-password', json=body)).status_code, 400)
        await self.login(NEW_PASSWORD)

    async def test_invalid_reset_password_does_not_consume_code(self):
        code = await self.make_reset()
        body = {'email': self.user.email, 'code': code, 'new_password': 'short'}
        self.assertEqual((await self.client.post('/auth/reset-password', json=body)).status_code, 400)
        body['new_password'] = NEW_PASSWORD
        self.assertEqual((await self.client.post('/auth/reset-password', json=body)).status_code, 200)

    async def test_legacy_short_password_login_still_works(self):
        # Existing hashes are not invalidated just for falling below today's policy.
        await self.manager.user_db.update(self.user, {'hashed_password': self.manager.password_helper.hash('old')})
        await self.login('old')

    async def test_legacy_reset_link_revoked_after_password_change(self):
        reset = auth.user_create_reset_token(self.user)
        await self.manager._update(self.user, {'password': NEW_PASSWORD})
        with self.assertRaises(exceptions.InvalidResetPasswordToken):
            await auth.user_from_reset_token(reset)

    async def test_passwordless_trusted_creation_uses_random_password(self):
        user = await auth.user_create('trusted@example.com')
        self.assertFalse(self.manager.password_helper.verify_and_update('', user.hashed_password)[0])


class MigrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_existing_sqlite_migration_is_idempotent(self):
        engine = create_async_engine(f'sqlite+aiosqlite:///{_TMP.name}/old.db')
        async with engine.begin() as conn:
            await conn.execute(text('CREATE TABLE "user" (id TEXT PRIMARY KEY, email TEXT)'))
            await conn.execute(text("INSERT INTO \"user\" VALUES ('fixture', 'old@example.com')"))
        with patch.object(db, 'engine', engine):
            await db.create_db_and_tables()
            await db.create_db_and_tables()
        async with engine.connect() as conn:
            row = (await conn.execute(text('SELECT email, token_version FROM "user"'))).one()
            self.assertEqual(tuple(row), ('old@example.com', 0))
        await engine.dispose()


if __name__ == '__main__':
    unittest.main(verbosity=2)
