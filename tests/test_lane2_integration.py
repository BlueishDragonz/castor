"""Real route/middleware integration using a disposable DB, no live accounts."""
import os
import tempfile
import unittest
from unittest.mock import patch

_TMP = tempfile.TemporaryDirectory()
os.environ['DATABASE_URL'] = f'sqlite+aiosqlite:///{_TMP.name}/integration.db'
os.environ['JWT_SECRET'] = 'fixture-jwt-key-not-used-outside-tests-32'
os.environ['RESET_PASSWORD_TOKEN_SECRET'] = 'fixture-reset-key-not-used-outside-tests-32'
os.environ['NICEGUI_STORAGE_SECRET'] = 'fixture-storage-key-not-used-outside-tests-32'
os.environ['JWT_LIFETIME_SECONDS'] = '3600'
os.environ['TRUSTED_LOCAL_EMAIL'] = ''
os.environ['TRUSTED_EMAIL_HEADER'] = ''
os.environ['REQUIRE_ADMIN_FOR_REGISTRATION'] = 'false'

import httpx
import beaverhabits.main
from beaverhabits.app import db, auth
from beaverhabits.configs import settings


class RouteTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        async with db.engine.begin() as conn:
            await conn.run_sync(db.Base.metadata.drop_all)
        await db.create_db_and_tables()
        self.user = await auth.user_create('integration@example.com', 'test-only-long-password')
        self.token = await auth.user_create_token(self.user)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=beaverhabits.main.app), base_url='http://test')

    async def asyncTearDown(self):
        await self.client.aclose()
        await db.engine.dispose()

    async def test_real_api_user_limit(self):
        with patch.object(settings,'API_RATE_USER_PER_MINUTE',2):
            headers={'Authorization':'Bearer '+self.token}
            results=[await self.client.get('/api/v1/habits/export',headers=headers) for _ in range(3)]
        self.assertEqual([r.status_code for r in results],[200,200,429])
        self.assertIn('retry-after',results[-1].headers)

    async def test_real_api_ip_limit_ignores_forwarded_header(self):
        with patch.object(settings,'API_RATE_IP_PER_MINUTE',2):
            results=[await self.client.get('/api/v1/habits/export',headers={'X-Forwarded-For':f'192.0.2.{i}'}) for i in range(3)]
        self.assertEqual([r.status_code for r in results],[401,401,429])

    async def test_real_route_origin_guard(self):
        r=await self.client.patch('/users/me',headers={'Origin':'http://other.example','Authorization':'Bearer '+self.token},json={'password':'another-test-password'})
        self.assertEqual(r.status_code,403)
        self.assertIsNotNone(await auth.user_from_token(self.token))

    async def test_real_native_password_login(self):
        r=await self.client.post('/auth/login',data={'username':self.user.email,'password':'test-only-long-password'})
        self.assertEqual(r.status_code,200)
        self.assertIn('access_token',r.json())

    async def test_auth_ip_limit(self):
        with patch.object(settings,'AUTH_RATE_IP_PER_MINUTE',1):
            a=await self.client.post('/auth/login',data={'username':self.user.email,'password':'test-only-long-password'})
            b=await self.client.post('/auth/login',data={'username':self.user.email,'password':'test-only-long-password'})
        self.assertEqual([a.status_code,b.status_code],[200,429])

if __name__ == '__main__': unittest.main()
