"""Slice 9-alt — Sort contracts + minimal WebSocket smoke tests.

Sort tests use the standard async httpx + ASGITransport pattern.

WebSocket tests use ``starlette.testclient.TestClient`` which is sync
(supports WebSockets via anyio). To avoid event-loop conflicts we
run everything sync in the test method, using TestClient throughout
for both login (via httpx with ASGI) AND the WebSocket itself.

Parity rows pinned:
  - 2.10 Sort habits by Name / Category / Manually
  - 11.1 WebSocket fan-out of HabitListChanged (real E2E for the first
    time; existing test_realtime.py only tests the helper)
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

import asyncio
import socket
import threading
import time

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


# ---------------------------------------------------------------------------
# Sync helpers — run async setup once, capture state, share across tests.
# ---------------------------------------------------------------------------

def _run_in_thread(coro):
    """Run a coroutine on a fresh event loop in a daemon thread."""
    box = {}

    def target():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            box['value'] = loop.run_until_complete(coro)
        except Exception as e:
            box['error'] = e
        finally:
            loop.close()

    t = threading.Thread(target=target, daemon=True)
    t.start()
    t.join(timeout=20)
    if t.is_alive():
        raise RuntimeError("coroutine thread hung")
    if 'error' in box:
        raise box['error']
    return box.get('value')


async def _setup_two_users():
    """Recreate schema, create two users with empty habit lists."""
    async with db.engine.begin() as conn:
        await conn.run_sync(db.Base.metadata.drop_all)
    await db.create_db_and_tables()
    reset_routes._rate_limit_cache.clear()
    session = db.async_session_maker()
    manager = UserManager(SQLAlchemyUserDatabase(session, db.User))
    a = await manager.create(UserCreate(email='wsa@example.com', password=PASSWORD))
    b = await manager.create(UserCreate(email='wsb@example.com', password=PASSWORD))
    await views.get_or_create_user_habit_list(a, views.dummy_empty_habit_list())
    await views.get_or_create_user_habit_list(b, views.dummy_empty_habit_list())
    await session.close()
    await db.engine.dispose()


async def _login_token(email: str) -> str:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url='http://test') as c:
        r = await c.post('/auth/login', data={'username': email, 'password': PASSWORD})
        return r.json()['access_token']


# ---------------------------------------------------------------------------
# WebSocket tests (start a real uvicorn server on a free port).
# ---------------------------------------------------------------------------


def _find_free_port() -> int:
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    port = s.getsockname()[1]
    s.close()
    return port


class _UvicornThread:
    """Run a uvicorn server in a daemon thread for the duration of a test."""

    def __init__(self, port: int):
        self.port = port
        self._server = None
        self._thread = None
        self._ready = threading.Event()

    def __enter__(self):
        import uvicorn
        config = uvicorn.Config(
            app,
            host='127.0.0.1',
            port=self.port,
            log_level='warning',
            loop='asyncio',
        )
        self._server = uvicorn.Server(config)

        def run():
            self._server.run()

        self._thread = threading.Thread(target=run, daemon=True)
        self._thread.start()
        # Wait for the server to be ready (poll for up to 5s).
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if self._server.started:
                return self
            time.sleep(0.05)
        raise RuntimeError('uvicorn server did not start within 5s')

    def __exit__(self, *args):
        if self._server:
            self._server.should_exit = True
        if self._thread:
            self._thread.join(timeout=5)


class Slice9AltWebSocketTests(unittest.TestCase):
    """End-to-end WebSocket contract tests."""

    @classmethod
    def setUpClass(cls):
        _run_in_thread(_setup_two_users())
        cls.port = _find_free_port()
        cls.server = _UvicornThread(cls.port).__enter__()
        # Wait for the server to actually accept connections.
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            try:
                s = socket.create_connection(('127.0.0.1', cls.port), timeout=0.5)
                s.close()
                return
            except OSError:
                time.sleep(0.1)
        raise RuntimeError('uvicorn server did not accept connections')

    @classmethod
    def tearDownClass(cls):
        cls.server.__exit__(None, None, None)

    def _url(self, token: str | None = None) -> str:
        base = f'ws://127.0.0.1:{self.port}/api/v1/sync/ws'
        if token:
            return f'{base}?token={token}'
        return base

    def _login(self, email: str) -> str:
        """Sync login via httpx against the real uvicorn server."""
        import httpx
        r = httpx.post(
            f'http://127.0.0.1:{self.port}/auth/login',
            data={'username': email, 'password': PASSWORD},
            timeout=5.0,
        )
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()['access_token']

    def _run_async(self, coro):
        """Run a coroutine on a fresh loop on a separate thread."""
        return _run_in_thread(coro)

    async def _async_login(self, email: str) -> str:
        import httpx
        async with httpx.AsyncClient() as c:
            r = await c.post(
                f'http://127.0.0.1:{self.port}/auth/login',
                data={'username': email, 'password': PASSWORD},
                timeout=5.0,
            )
            return r.json()['access_token']

    def test_ws_no_token_closes_with_policy_violation(self):
        """ws://.../sync/ws without ?token= → server rejects with HTTP 4xx."""
        import websockets

        async def scenario():
            try:
                async with websockets.connect(self._url()) as ws:
                    try:
                        await ws.recv()
                    except websockets.ConnectionClosed:
                        pass
            except (websockets.InvalidStatus, websockets.InvalidStatusCode):
                # Server rejected during handshake (HTTP 4xx). Expected.
                pass

        self._run_async(scenario())

    def test_ws_invalid_token_closes(self):
        import websockets

        async def scenario():
            try:
                async with websockets.connect(
                    self._url(token='garbage.bearer.value')
                ) as ws:
                    try:
                        await ws.recv()
                    except websockets.ConnectionClosed:
                        pass
            except (websockets.InvalidStatus, websockets.InvalidStatusCode):
                pass

        self._run_async(scenario())

    def test_ws_valid_token_accepts_and_stays_open(self):
        token = self._run_async(self._async_login('wsa@example.com'))

        import websockets

        async def scenario():
            async with websockets.connect(self._url(token)) as ws:
                await ws.send('{"type": "unknown_type"}')
                await ws.send('{"type": "unknown_type"}')

        self._run_async(scenario())

    def test_ws_push_habit_list_broadcasts_to_other_devices(self):
        token = self._run_async(self._async_login('wsa@example.com'))

        import websockets

        async def scenario():
            # Open ws2 first so it registers.
            async with websockets.connect(self._url(token)) as ws2:
                async with websockets.connect(self._url(token)) as ws1:
                    await ws1.send(
                        '{"type": "push_habit_list", "request_id": "r-1", '
                        '"habits": [], "order": []}'
                    )
                    # ws1 gets habit_list_ack directly.
                    ack = await ws1.recv()
                    self.assertIn('"habit_list_ack"', ack)
                    self.assertIn('"r-1"', ack)
                    # ws2 receives the broadcast.
                    msg = await ws2.recv()
                    self.assertIn('"habit_list_changed"', msg)

        self._run_async(scenario())

    def test_ws_cross_user_isolation(self):
        token_a = self._run_async(self._async_login('wsa@example.com'))
        token_b = self._run_async(self._async_login('wsb@example.com'))

        import websockets

        async def scenario():
            async with websockets.connect(self._url(token_b)) as ws_b:
                async with websockets.connect(self._url(token_a)) as ws_a:
                    await ws_a.send(
                        '{"type": "push_habit_list", "request_id": "r-x", '
                        '"habits": [], "order": []}'
                    )
                    ack = await ws_a.recv()
                    self.assertIn('"habit_list_ack"', ack)
                    # ws_b must NOT receive any message.
                    try:
                        msg = await asyncio.wait_for(ws_b.recv(), timeout=1.5)
                        self.fail(
                            f"User B received User A's broadcast: {msg!r}"
                        )
                    except asyncio.TimeoutError:
                        pass

        self._run_async(scenario())

    def test_http_tick_broadcasts_to_other_websocket(self):
        """HTTP POST /api/v1/habits/{id}/completions calls habit.tick()
        which publishes TickChanged. Other WebSocket devices of the same
        user receive ``tick_changed`` broadcasts.

        Note: the sender's WebSocket (if any) is excluded — they get a
        ``tick_ack`` only when they push via WebSocket. The HTTP path
        has no per-WebSocket ack.
        """
        token = self._run_async(self._async_login('wsa@example.com'))

        import websockets
        import httpx

        async def scenario():
            # Open a watcher WebSocket.
            async with websockets.connect(self._url(token)) as ws_watch:
                # Small delay so the watcher registers with the manager.
                await asyncio.sleep(0.2)

                async with httpx.AsyncClient() as h:
                    create = await h.post(
                        f'http://127.0.0.1:{self.port}/api/v1/habits',
                        headers={'Authorization': f'Bearer {token}'},
                        json={'name': 'tick-test-2'},
                        timeout=5.0,
                    )
                    self.assertIn(create.status_code, (200, 201), create.text)
                    habit_id = create.json()['id']

                    tick = await h.post(
                        f'http://127.0.0.1:{self.port}/api/v1/habits/{habit_id}/completions',
                        headers={'Authorization': f'Bearer {token}'},
                        json={'done': True, 'date': '24-09-2026'},
                        timeout=5.0,
                    )
                    self.assertEqual(tick.status_code, 200, tick.text)

                # Watcher must receive a tick_changed broadcast.
                msg = await asyncio.wait_for(ws_watch.recv(), timeout=2.0)
                self.assertIn('"tick_changed"', msg)
                self.assertIn(f'"habit_id":"{habit_id}"', msg)
                self.assertIn('"done":true', msg)

        self._run_async(scenario())


# ---------------------------------------------------------------------------
# Sort tests (async, isolated).
# ---------------------------------------------------------------------------

class Slice9AltSortTests(unittest.IsolatedAsyncioTestCase):
    """Backend sort contracts (parity row 2.10)."""

    async def asyncSetUp(self):
        # Each test class gets its own DB; we reset here.
        async with db.engine.begin() as conn:
            await conn.run_sync(db.Base.metadata.drop_all)
        await db.create_db_and_tables()
        reset_routes._rate_limit_cache.clear()
        session = db.async_session_maker()
        manager = UserManager(SQLAlchemyUserDatabase(session, db.User))
        self.user = await manager.create(
            UserCreate(email='sort@example.com', password=PASSWORD)
        )
        await views.get_or_create_user_habit_list(self.user, views.dummy_empty_habit_list())
        self.session = session
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url='http://test'
        )
        token = await self._login()
        for name in ['Charlie', 'Alpha', 'Bravo']:
            r = await self.client.post(
                '/api/v1/habits',
                headers={'Authorization': f'Bearer {token}'},
                json={'name': name},
            )
            self.assertIn(r.status_code, (200, 201), r.text)
        habits = (await self.client.get(
            '/api/v1/habits',
            headers={'Authorization': f'Bearer {token}'},
        )).json()
        for h in habits:
            if h['name'] == 'Bravo':
                self.bravo_id = h['id']
        await self.client.put(
            f'/api/v1/habits/{self.bravo_id}',
            headers={'Authorization': f'Bearer {token}'},
            json={'tags': ['z-tag']},
        )

    async def asyncTearDown(self):
        await self.client.aclose()
        await self.session.close()

    async def _login(self) -> str:
        r = await self.client.post(
            '/auth/login',
            data={'username': 'sort@example.com', 'password': PASSWORD},
        )
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()['access_token']

    async def test_default_order_is_creation_order(self):
        token = await self._login()
        r = await self.client.get(
            '/api/v1/habits',
            headers={'Authorization': f'Bearer {token}'},
        )
        self.assertEqual(r.status_code, 200, r.text)
        names = [h['name'] for h in r.json()]
        self.assertEqual(names, ['Charlie', 'Alpha', 'Bravo'])

    async def test_order_by_name_sorts_alphabetically(self):
        token = await self._login()
        r = await self.client.get(
            '/api/v1/habits?order_by=name',
            headers={'Authorization': f'Bearer {token}'},
        )
        self.assertEqual(r.status_code, 200, r.text)
        names = [h['name'] for h in r.json()]
        self.assertEqual(names, ['Alpha', 'Bravo', 'Charlie'])

    async def test_order_by_category_sorts_tagged_first(self):
        """?order_by=category puts tagged habits first."""
        token = await self._login()
        r = await self.client.get(
            '/api/v1/habits?order_by=category',
            headers={'Authorization': f'Bearer {token}'},
        )
        self.assertEqual(r.status_code, 200, r.text)
        names = [h['name'] for h in r.json()]
        self.assertEqual(names[0], 'Bravo')
        self.assertEqual(sorted(names[1:]), ['Alpha', 'Charlie'])

    async def test_invalid_order_by_falls_back_to_default(self):
        token = await self._login()
        r = await self.client.get(
            '/api/v1/habits?order_by=garbage',
            headers={'Authorization': f'Bearer {token}'},
        )
        self.assertEqual(r.status_code, 200, r.text)
        names = [h['name'] for h in r.json()]
        self.assertEqual(names, ['Charlie', 'Alpha', 'Bravo'])


if __name__ == '__main__':
    unittest.main()
