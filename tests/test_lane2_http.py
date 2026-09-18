"""Defensive HTTP-policy tests; disposable DB and in-process ASGI only."""
import asyncio
import tempfile
import unittest
from pathlib import Path

import httpx
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker


class OriginTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from beaverhabits.app.http_security import BrowserOriginMiddleware
        app = FastAPI()
        @app.post('/write')
        async def write():
            return {'ok': True}
        @app.get('/read')
        async def read():
            return {'ok': True}
        app.add_middleware(BrowserOriginMiddleware, allowed_origins=['https://tracker.example'])
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='https://tracker.example')

    async def asyncTearDown(self):
        await self.client.aclose()

    async def test_browser_same_origin(self):
        r = await self.client.post('/write', headers={'Origin':'https://tracker.example','Cookie':'beaver_auth=test'})
        self.assertEqual(r.status_code, 200)

    async def test_browser_cross_origin_is_rejected(self):
        r = await self.client.post('/write', headers={'Origin':'https://other.example'})
        self.assertEqual(r.status_code, 403)

    async def test_opaque_origin_is_rejected(self):
        self.assertEqual((await self.client.post('/write', headers={'Origin':'null'})).status_code,403)

    async def test_missing_origin_cookie_denied(self):
        self.assertEqual((await self.client.post('/write', headers={'Cookie':'session=test'})).status_code,403)

    async def test_native_bearer_without_cookie_kept(self):
        self.assertEqual((await self.client.post('/write', headers={'Authorization':'Bearer fixture'})).status_code,200)

    async def test_native_no_cookie_kept(self):
        self.assertEqual((await self.client.post('/write')).status_code,200)

    async def test_safe_get_kept(self):
        self.assertEqual((await self.client.get('/read', headers={'Cookie':'session=test'})).status_code,200)

    async def test_origin_matches_whole_origin(self):
        self.assertEqual((await self.client.post('/write', headers={'Origin':'https://tracker.example:444'})).status_code,403)

    async def test_referer_fallback(self):
        self.assertEqual((await self.client.post('/write', headers={'Cookie':'session=test','Referer':'https://tracker.example/gui'})).status_code,200)

    async def test_fetch_metadata_cross_site(self):
        self.assertEqual((await self.client.post('/write',headers={'Sec-Fetch-Site':'cross-site'})).status_code,403)

    async def test_ws_origin_gate(self):
        from beaverhabits.app.http_security import BrowserOriginMiddleware
        messages=[]
        async def inner(scope,receive,send):
            await send({'type':'websocket.accept'})
        async def send(msg): messages.append(msg)
        async def receive(): return {'type':'websocket.connect'}
        wrapper=BrowserOriginMiddleware(inner,allowed_origins=['https://tracker.example'])
        await wrapper({'type':'websocket','scheme':'wss','path':'/_nicegui_ws/socket.io','headers':[(b'host',b'tracker.example'),(b'origin',b'https://other.example')]},receive,send)
        self.assertEqual(messages,[{'type':'websocket.close','code':1008}])


class LimiterTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from beaverhabits.app.rate_limits import RateBucket
        self.tmp=tempfile.TemporaryDirectory()
        self.engine=create_async_engine('sqlite+aiosqlite:///'+str(Path(self.tmp.name)/'limit.db'))
        self.sessions=async_sessionmaker(self.engine,expire_on_commit=False)
        async with self.engine.begin() as conn:
            await conn.run_sync(RateBucket.__table__.create)

    async def asyncTearDown(self):
        await self.engine.dispose()
        self.tmp.cleanup()

    async def test_threshold_expiry_and_independent_identity(self):
        from beaverhabits.app.rate_limits import consume
        self.assertTrue(await consume('user','one',2,60,now=120,sessions=self.sessions))
        self.assertTrue(await consume('user','one',2,60,now=120,sessions=self.sessions))
        self.assertFalse(await consume('user','one',2,60,now=120,sessions=self.sessions))
        self.assertTrue(await consume('user','two',2,60,now=120,sessions=self.sessions))
        self.assertTrue(await consume('user','one',2,60,now=180,sessions=self.sessions))

    async def test_atomic_limit_across_connections(self):
        from beaverhabits.app.rate_limits import consume
        got=await asyncio.gather(*(consume('ip','one',3,60,now=120,sessions=self.sessions) for _ in range(10)))
        self.assertEqual(sum(got),3)

    async def test_persisted_and_no_raw_identifier(self):
        from beaverhabits.app.rate_limits import consume, RateBucket
        from sqlalchemy import select
        await consume('ip','192.0.2.4',1,60,now=120,sessions=self.sessions)
        sessions=async_sessionmaker(self.engine,expire_on_commit=False)
        self.assertFalse(await consume('ip','192.0.2.4',1,60,now=120,sessions=sessions))
        async with sessions() as session:
            keys=(await session.execute(select(RateBucket.key))).scalars().all()
        self.assertTrue(all('192.0.2.4' not in k for k in keys))

    async def test_namespace_pressure_does_not_throttle_other_namespaces(self):
        # P1-5: the cardinality cap must be per-namespace. A recovery flood
        # approaching the shared 10k row cap must not evict/block API traffic.
        from beaverhabits.app.rate_limits import consume, RateBucket, CARDINALITY_CAP
        from sqlalchemy import insert
        # Fill 'recovery' namespace to the cap: one row per distinct identity.
        async with self.sessions.begin() as session:
            await session.execute(insert(RateBucket).values([
                {'key': f'recovery:{i:06d}:{99999}', 'expires_at': 99999, 'count': 1}
                for i in range(CARDINALITY_CAP)
            ]))
        # A fresh recovery identity is rejected by its own namespace cap...
        self.assertFalse(await consume('recovery', 'flooded-newcomer', 5, 60, now=120, sessions=self.sessions))
        # ...but api-ip and auth-ip namespaces stay fully admitted.
        self.assertTrue(await consume('api-ip', '192.0.2.10', 5, 60, now=120, sessions=self.sessions))
        self.assertTrue(await consume('auth-ip', '192.0.2.10', 5, 60, now=120, sessions=self.sessions))
        self.assertTrue(await consume('api-user', 'user-1', 5, 60, now=120, sessions=self.sessions))

    async def test_cardinality_cap_counts_within_namespace_only(self):
        # P1-5: the count-then-insert honesty part — the cap query must count
        # rows of THIS namespace, not the whole table.
        from beaverhabits.app.rate_limits import consume, RateBucket, CARDINALITY_CAP
        from sqlalchemy import insert
        half = CARDINALITY_CAP // 2
        async with self.sessions.begin() as session:
            await session.execute(insert(RateBucket).values([
                {'key': f'recovery:a{i:06d}:{99999}', 'expires_at': 99999, 'count': 1}
                for i in range(half)
            ] + [
                {'key': f'api-ip:b{i:06d}:{99999}', 'expires_at': 99999, 'count': 1}
                for i in range(half)
            ]))
        # Total rows == cap, but neither namespace alone is at its own cap.
        self.assertTrue(await consume('recovery', 'newcomer', 5, 60, now=120, sessions=self.sessions))
        self.assertTrue(await consume('api-ip', 'newcomer', 5, 60, now=120, sessions=self.sessions))


if __name__ == '__main__': unittest.main()
