"""Defensive recovery invariants; disposable databases, no external mail or accounts.

Run separately: .venv/bin/python -m unittest tests.test_recovery_code_safety -v
"""
import asyncio
import datetime as dt
import os
import secrets
import tempfile
import threading
import unittest
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch
from uuid import uuid4

_BOOT = tempfile.TemporaryDirectory()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_BOOT.name}/unused.db"
for _name in ("JWT_SECRET", "RESET_PASSWORD_TOKEN_SECRET", "NICEGUI_STORAGE_SECRET"):
    os.environ[_name] = secrets.token_urlsafe(32)
os.environ["TRUSTED_LOCAL_EMAIL"] = ""
os.environ["TRUSTED_EMAIL_HEADER"] = ""
os.environ["SENTRY_DSN"] = ""

import dotenv
with patch.object(dotenv, "load_dotenv", return_value=False):
    import beaverhabits.main as main

import httpx
from fastapi import FastAPI
from fastapi_users.db import SQLAlchemyUserDatabase
from sqlalchemy import event, inspect, select, text, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from starlette.requests import Request
from beaverhabits.app import db, rate_limits, reset_routes as routes
from beaverhabits.app.users import UserManager

PASSWORD = "replacement fixture passphrase"
UTC = dt.timezone.utc


class RecoverySafetyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.engine = create_async_engine(f"sqlite+aiosqlite:///{self.tmp.name}/recovery.db")
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        self.patches = [patch.object(db, "engine", self.engine),
                        patch.object(db, "async_session_maker", self.sessions),
                        patch.object(rate_limits, "async_session_maker", self.sessions),
                        patch("beaverhabits.app.audit.record", new_callable=AsyncMock)]
        for item in self.patches:
            item.start()
        await db.create_db_and_tables()
        if hasattr(routes, "_rate_limit_cache"):
            routes._rate_limit_cache.clear()
        self.user = db.User(id=uuid4(), email="fixture@example.com", hashed_password="unused", token_version=4, is_active=True)
        async with self.sessions.begin() as session:
            session.add(self.user)
        app = FastAPI()
        app.include_router(routes.router)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
        self.request = Request({"type": "http", "headers": [], "client": ("127.0.0.1", 1)})

    async def asyncTearDown(self):
        await self.client.aclose()
        for item in reversed(self.patches):
            item.stop()
        await self.engine.dispose()
        self.tmp.cleanup()

    async def make_code(self, *, bound=True, **changes):
        code = routes._generate_code()
        values = dict(email=self.user.email, code_hash=routes._hash_code(code),
                      expires_at=dt.datetime.now(UTC) + dt.timedelta(minutes=5), used=False)
        if bound:
            self.assertTrue(hasattr(db.PasswordResetCode, "user_id"), "Reset rows must bind immutable user identity")
            self.assertTrue(hasattr(db.PasswordResetCode, "token_version"), "Reset rows must bind issuance version")
            values.update(user_id=self.user.id, token_version=self.user.token_version)
        values.update(changes)
        async with self.sessions.begin() as session:
            row = db.PasswordResetCode(**values)
            session.add(row)
        return code, row.id

    async def reset(self, code, password=PASSWORD, email=None):
        return await self.client.post("/auth/reset-password", json={
            "email": email or self.user.email, "code": code, "new_password": password})

    async def state(self, row_id):
        async with self.sessions() as session:
            user = await session.get(db.User, self.user.id)
            row = await session.get(db.PasswordResetCode, row_id)
            return user.hashed_password, user.token_version, row.used

    async def test_unbound_legacy_code_rejected(self):
        code, row_id = await self.make_code(bound=False)
        self.assertEqual((await self.reset(code)).status_code, 400)
        self.assertEqual(await self.state(row_id), ("unused", 4, False))

    async def test_expired_code_rejected(self):
        code, row_id = await self.make_code(expires_at=dt.datetime.now(UTC) - dt.timedelta(seconds=1))
        self.assertEqual((await self.reset(code)).status_code, 400)
        self.assertEqual(await self.state(row_id), ("unused", 4, False))

    async def test_bound_reset_single_use_hash_and_sibling_revocation(self):
        code, row_id = await self.make_code()
        sibling, sibling_id = await self.make_code()
        with patch.object(SQLAlchemyUserDatabase, "update", side_effect=AssertionError("No independently committing manager update")):
            self.assertEqual((await self.reset(code)).status_code, 200)
        hashed, version, used = await self.state(row_id)
        self.assertTrue(UserManager(None).password_helper.verify_and_update(PASSWORD, hashed)[0])
        self.assertEqual((version, used), (5, True))
        self.assertEqual((await self.reset(code)).status_code, 400)
        self.assertEqual((await self.reset(sibling)).status_code, 400)
        self.assertFalse((await self.state(sibling_id))[2])  # logically invalidated, not bulk rewritten

    async def test_policy_rejection_precedes_hash_and_consumption(self):
        code, row_id = await self.make_code()
        with patch.object(UserManager(None).password_helper.__class__, "hash", side_effect=AssertionError("Policy must run before hashing")):
            self.assertEqual((await self.reset(code, password="short")).status_code, 400)
        self.assertEqual(await self.state(row_id), ("unused", 4, False))
        self.assertEqual((await self.reset(code)).status_code, 200)

    async def test_binding_active_and_version_fail_closed(self):
        for changes in ({"user_id": uuid4()}, {"user_id": None}, {"token_version": None}, {"token_version": 3}):
            with self.subTest(changes=list(changes)):
                code, row_id = await self.make_code(**changes)
                # Bypass throttling only in invariant-focused tests.
                with patch.object(routes, "_rate_limit_check", new_callable=AsyncMock):
                    self.assertEqual((await self.reset(code)).status_code, 400)
                self.assertEqual(await self.state(row_id), ("unused", 4, False))
        code, row_id = await self.make_code()
        async with self.sessions.begin() as session:
            await session.execute(update(db.User).where(db.User.id == self.user.id).values(is_active=False))
        with patch.object(routes, "_rate_limit_check", new_callable=AsyncMock):
            self.assertEqual((await self.reset(code)).status_code, 400)
        self.assertEqual(await self.state(row_id), ("unused", 4, False))

    async def test_concurrent_resets_only_one_success_across_engines(self):
        for siblings in (False, True):
            async with self.sessions.begin() as session:
                await session.execute(update(db.User).where(db.User.id == self.user.id).values(token_version=4, hashed_password="unused"))
            code, row_id = await self.make_code()
            other, other_id = await self.make_code() if siblings else (code, row_id)
            second_engine = create_async_engine(self.engine.url)
            second_sessions = async_sessionmaker(second_engine, expire_on_commit=False)
            factories = {}
            ready = asyncio.Event()
            hashes_ready = threading.Barrier(2, timeout=10)
            helper_type = UserManager(None).password_helper.__class__
            original_hash = helper_type.hash
            def simultaneous_hash(helper, password):
                # Both requests have read the same valid issuance version before
                # either can write; a serial replay test would miss the CAS race.
                hashes_ready.wait()
                return original_hash(helper, password)
            @asynccontextmanager
            async def alternating_sessions():
                task = asyncio.current_task()
                if task not in factories:
                    factories[task] = self.sessions if not factories else second_sessions
                if len(factories) == 2:
                    ready.set()
                await ready.wait()
                async with factories[task]() as session:
                    yield session
            try:
                with patch.object(routes, "get_async_session_context", alternating_sessions), patch.object(routes, "_rate_limit_check", new_callable=AsyncMock), patch.object(helper_type, "hash", simultaneous_hash):
                    results = await asyncio.gather(self.reset(code), self.reset(other))
                self.assertEqual(sorted(r.status_code for r in results), [200, 400])
                states = [await self.state(i) for i in {row_id, other_id}]
                self.assertEqual(sum(used for _, _, used in states), 1)
                self.assertTrue(all(version == 5 for _, version, _ in states))
            finally:
                await second_engine.dispose()

    async def test_mutable_state_rechecked_after_hashing(self):
        for mutation in ("expiry", "used", "version", "inactive"):
            with self.subTest(mutation=mutation):
                async with self.sessions.begin() as session:
                    await session.execute(update(db.User).where(db.User.id == self.user.id).values(token_version=4, is_active=True, hashed_password="unused"))
                code, row_id = await self.make_code()
                real_to_thread = asyncio.to_thread
                async def mutate_during_hash(function, *args, **kwargs):
                    hashed = await real_to_thread(function, *args, **kwargs)
                    async with self.sessions.begin() as session:
                        if mutation == "expiry":
                            await session.execute(update(db.PasswordResetCode).where(db.PasswordResetCode.id == row_id).values(expires_at=dt.datetime.now(UTC) - dt.timedelta(seconds=1)))
                        elif mutation == "used":
                            await session.execute(update(db.PasswordResetCode).where(db.PasswordResetCode.id == row_id).values(used=True))
                        elif mutation == "version":
                            await session.execute(update(db.User).where(db.User.id == self.user.id).values(token_version=5))
                        else:
                            await session.execute(update(db.User).where(db.User.id == self.user.id).values(is_active=False))
                    return hashed
                with patch.object(routes.asyncio, "to_thread", mutate_during_hash), patch.object(routes, "_rate_limit_check", new_callable=AsyncMock):
                    self.assertEqual((await self.reset(code)).status_code, 400)
                self.assertEqual(await self.state(row_id), ("unused", 5 if mutation == "version" else 4, mutation == "used"))

    async def test_actual_outer_auth_throttle_covers_recovery(self):
        from beaverhabits.configs import settings
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test") as client:
            with patch.object(settings, "AUTH_RATE_IP_PER_MINUTE", 1), patch.object(routes, "send_email") as mail:
                first = await client.post("/auth/forgot-password", json={"email": "missing@example.com"})
                second = await client.post("/auth/forgot-password", json={"email": "other@example.com"}, headers={"X-Forwarded-For": "192.0.2.88"})
            self.assertEqual((first.status_code, second.status_code), (200, 429))
            mail.assert_not_called()

    async def test_rate_store_failure_is_closed_and_safe(self):
        from fastapi import HTTPException
        with patch.object(routes, "consume", new=AsyncMock(side_effect=RuntimeError("PRIVATE-STORE-ERROR"))):
            with self.assertRaises(HTTPException) as raised:
                await routes._rate_limit_check("fixture", 1, 60)
        self.assertEqual(raised.exception.status_code, 503)
        self.assertNotIn("PRIVATE", raised.exception.detail)

    async def test_consumption_failure_rolls_back_password_and_version(self):
        code, row_id = await self.make_code()
        async with self.engine.begin() as conn:
            await conn.execute(text("CREATE TRIGGER reject_reset BEFORE UPDATE OF used ON password_reset_code BEGIN SELECT RAISE(ABORT, 'fixture fault'); END"))
        with self.assertRaises(Exception):
            await self.reset(code)
        self.assertEqual(await self.state(row_id), ("unused", 4, False))

    async def test_commit_failure_rolls_back_both_writes(self):
        code, row_id = await self.make_code()
        def fail_commit(connection):
            raise RuntimeError("fixture commit fault")
        event.listen(self.engine.sync_engine, "commit", fail_commit)
        try:
            with patch.object(routes, "_rate_limit_check", new_callable=AsyncMock), self.assertRaises(RuntimeError):
                await self.reset(code)
        finally:
            event.remove(self.engine.sync_engine, "commit", fail_commit)
        self.assertEqual(await self.state(row_id), ("unused", 4, False))

    async def test_issue_binds_identity_version_and_awaits_threaded_mail(self):
        calls = []
        main_thread = threading.get_ident()
        def mail(*args, **kwargs):
            calls.append(threading.get_ident())
        with patch.object(routes, "send_email", side_effect=mail), patch.object(routes, "logger") as logger:
            response = await self.client.post("/auth/forgot-password", json={"email": self.user.email})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(calls), 1)
        self.assertNotEqual(calls[0], main_thread)
        logger.exception.assert_not_called()
        async with self.sessions() as session:
            row = (await session.scalars(select(db.PasswordResetCode))).one()
            self.assertEqual((row.user_id, row.token_version), (self.user.id, 4))

    async def test_mail_error_is_generic_without_exception_or_body(self):
        def failing_mail(*args, **kwargs):
            raise RuntimeError("PRIVATE-MAIL-BODY " + args[1])
        with patch.object(routes, "send_email", side_effect=failing_mail), patch.object(routes, "logger") as logger:
            response = await self.client.post("/auth/forgot-password", json={"email": self.user.email})
        self.assertEqual(response.status_code, 200)
        logger.exception.assert_not_called()
        self.assertTrue(logger.warning.called or logger.error.called)
        # No raw exception, traceback, recipient or mail body in any logging call.
        logged = str(logger.mock_calls)
        self.assertNotIn("PRIVATE-MAIL-BODY", logged)
        self.assertNotIn(self.user.email, logged)
        self.assertNotIn("exc_info", logged)

    async def test_inactive_or_unknown_request_has_same_response_and_no_mail(self):
        async with self.sessions.begin() as session:
            await session.execute(update(db.User).where(db.User.id == self.user.id).values(is_active=False))
        with patch.object(routes, "send_email") as mail:
            inactive = await self.client.post("/auth/forgot-password", json={"email": self.user.email})
            unknown = await self.client.post("/auth/forgot-password", json={"email": "missing@example.com"})
        self.assertEqual(inactive.json(), unknown.json())
        mail.assert_not_called()
        async with self.sessions() as session:
            self.assertIsNone(await session.scalar(select(db.PasswordResetCode.id)))

    async def test_forwarded_ip_is_not_trusted(self):
        request = Request({"type": "http", "headers": [(b"x-forwarded-for", b"192.0.2.99")], "client": ("127.0.0.1", 1)})
        self.assertEqual(routes._get_client_ip(request), "127.0.0.1")

    async def test_existing_outer_middleware_and_persistent_route_limits(self):
        self.assertIn(rate_limits.IPRateLimitMiddleware, [m.cls for m in main.app.user_middleware])
        await routes._rate_limit_check("fixture:email", limit=1, window=900)
        if hasattr(routes, "_rate_limit_cache"):
            routes._rate_limit_cache.clear()  # process-local state must not reset admission
        second = create_async_engine(self.engine.url)
        try:
            with patch.object(rate_limits, "async_session_maker", async_sessionmaker(second)):
                from fastapi import HTTPException
                with self.assertRaises(HTTPException) as raised:
                    await routes._rate_limit_check("fixture:email", limit=1, window=900)
                self.assertEqual(raised.exception.status_code, 429)
        finally:
            await second.dispose()

    async def test_legacy_schema_migration_idempotent_and_unbound_invalidated(self):
        engine = create_async_engine(f"sqlite+aiosqlite:///{self.tmp.name}/legacy.db")
        try:
            async with engine.begin() as conn:
                await conn.execute(text("CREATE TABLE password_reset_code (id INTEGER PRIMARY KEY, email TEXT, code_hash TEXT, expires_at DATETIME, used BOOLEAN, created_at DATETIME, updated_at DATETIME)"))
                await conn.execute(text("INSERT INTO password_reset_code (id,email,code_hash,used) VALUES (1,'fixture@example.com','fixture-digest',0)"))
            with patch.object(db, "engine", engine):
                await db.create_db_and_tables()
                await db.create_db_and_tables()
            async with engine.connect() as conn:
                columns = await conn.run_sync(lambda c: {v["name"]: v for v in inspect(c).get_columns("password_reset_code")})
                self.assertIn("user_id", columns)
                self.assertIn("token_version", columns)
                self.assertTrue(columns["user_id"]["nullable"])
                self.assertTrue(columns["token_version"]["nullable"])
                row = (await conn.execute(text("SELECT user_id, token_version, used FROM password_reset_code"))).one()
                self.assertEqual(tuple(row), (None, None, 1))
        finally:
            await engine.dispose()


if __name__ == "__main__":
    unittest.main(verbosity=2)
