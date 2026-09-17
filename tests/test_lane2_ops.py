"""Source-only isolated operations tests: python -m unittest tests.test_lane2_ops -v."""
import asyncio
import json
import sqlite3
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy import delete, insert, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from beaverhabits.app.db import Base, User
from beaverhabits.app.audit import AuditEvent, append_audit_event, prune_audit_events
from beaverhabits.integrity import check_sqlite_integrity, daily_integrity_task


class AuditTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.engine = create_async_engine(f"sqlite+aiosqlite:///{self.tmp.name}/audit.db")
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    async def asyncTearDown(self):
        await self.engine.dispose()
        self.tmp.cleanup()

    async def test_persist_and_preserve_after_user_deletion(self):
        user_id = uuid4()
        async with self.sessions.begin() as session:
            session.add(User(id=user_id, email="fixture@example.invalid", hashed_password="unused"))
        await append_audit_event("account_delete", "success", user_id, session_factory=self.sessions)
        async with self.sessions.begin() as session:
            await session.execute(delete(User).where(User.id == user_id))
        async with self.sessions() as session:
            event = (await session.scalars(select(AuditEvent))).one()
            self.assertEqual((event.event, event.outcome, event.user_id), ("account_delete", "success", str(user_id)))
            self.assertIsNotNone(event.created_at)
        self.assertFalse(AuditEvent.__table__.foreign_keys)
        self.assertEqual(set(AuditEvent.__table__.columns.keys()), {"id", "created_at", "event", "outcome", "user_id"})

    async def test_reject_arbitrary_data_without_echoing_it(self):
        for args in [("secret@example.invalid", "success", None), ("login", "secret", None), ("login", "failure", "secret@example.invalid")]:
            with self.assertRaises(ValueError) as raised:
                await append_audit_event(*args, session_factory=self.sessions)
            self.assertNotIn("secret", str(raised.exception))
        with self.assertRaises(TypeError):
            await append_audit_event("login", "success", metadata={"password": "not accepted"})

    async def test_database_constraints_not_just_varchar(self):
        for changes in [{"event": "x" * 100}, {"outcome": "arbitrary"}, {"user_id": "x" * 100}]:
            with self.assertRaises(IntegrityError):
                async with self.sessions.begin() as session:
                    await session.execute(insert(AuditEvent).values(
                        **({"event": "login", "outcome": "failure"} | changes)
                    ))

    async def test_append_prunes_and_explicit_idle_pruning(self):
        now = datetime.now(timezone.utc)
        async with self.sessions.begin() as session:
            session.add_all([AuditEvent(event="login", outcome="success", created_at=now - timedelta(days=91)), AuditEvent(event="login", outcome="success", created_at=now - timedelta(days=89))])
        await append_audit_event("login", "failure", session_factory=self.sessions)
        async with self.sessions() as session:
            self.assertEqual(len((await session.scalars(select(AuditEvent))).all()), 2)
        self.assertEqual(await prune_audit_events(session_factory=self.sessions, retention_days=1), 1)
        for days in [0, -1, True, 1.5]:
            with self.assertRaises(ValueError):
                await prune_audit_events(session_factory=self.sessions, retention_days=days)


class IntegrityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db = self.root / "database #1.db"
        with sqlite3.connect(self.db) as connection:
            connection.execute("CREATE TABLE fixture (value TEXT)")
            connection.execute("INSERT INTO fixture VALUES ('private fixture')")
        self.url = f"sqlite+aiosqlite:///{self.db}"
        self.result = self.root / "status.json"

    def tearDown(self):
        self.tmp.cleanup()

    async def test_read_only_durable_atomic_and_off_event_loop(self):
        before = self.db.read_bytes()
        original_connect = sqlite3.connect
        calls = []
        def connect(*args, **kwargs):
            calls.append((threading.get_ident(), args, kwargs))
            connection = original_connect(*args, **kwargs)
            connection.set_trace_callback(lambda statement: calls.append(statement))
            return connection
        with patch("beaverhabits.integrity.sqlite3.connect", side_effect=connect):
            result = await check_sqlite_integrity(self.url, result_path=self.result)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(json.loads(self.result.read_text()), result)
        self.assertEqual(self.db.read_bytes(), before)
        self.assertNotEqual(calls[0][0], threading.get_ident())
        self.assertIn("mode=ro", calls[0][1][0])
        self.assertTrue(calls[0][2]["uri"])
        self.assertIn("PRAGMA integrity_check", calls)
        self.assertIn("PRAGMA query_only=ON", calls)
        self.assertNotIn("private fixture", self.result.read_text())
        self.assertEqual(self.result.stat().st_mode & 0o777, 0o600)

    async def test_corruption_and_missing_db_logged_and_persisted(self):
        for data in [b"not a sqlite database", None]:
            if data is None:
                self.db.unlink()
            else:
                self.db.write_bytes(data)
            with patch("beaverhabits.integrity.logger") as logger:
                result = await check_sqlite_integrity(self.url, result_path=self.result)
            self.assertEqual(result["status"], "error")
            logger.error.assert_called()
            self.assertEqual(json.loads(self.result.read_text()), result)
        self.assertFalse(self.db.exists())

    async def test_non_ok_pragma_is_failed_without_raw_diagnostics(self):
        with patch("beaverhabits.integrity.sqlite3.connect") as connect, patch("beaverhabits.integrity.logger") as logger:
            connect.return_value.execute.return_value.fetchall.return_value = [("private table diagnostic",)]
            result = await check_sqlite_integrity(self.url, result_path=self.result)
        self.assertEqual(result["status"], "failed")
        self.assertNotIn("private", self.result.read_text())
        logger.error.assert_called()

    async def test_skip_non_sqlite_and_memory_without_connecting(self):
        with patch("beaverhabits.integrity.sqlite3.connect") as connect:
            for url in ["postgresql+asyncpg://user:private@localhost/database", "sqlite+aiosqlite:///:memory:", "sqlite://", "sqlite:///file:fixture?mode=memory&uri=true"]:
                result = await check_sqlite_integrity(url, result_path=self.result)
                self.assertEqual(result["status"], "skipped")
                self.assertNotIn("private", self.result.read_text())
            connect.assert_not_called()

    async def test_engine_url_and_uri_target(self):
        engine = create_async_engine(self.url)
        try:
            with patch("beaverhabits.app.db.engine", engine):
                result = await check_sqlite_integrity(result_path=self.result)
            self.assertEqual(result["status"], "ok")
            url = "sqlite:///" + self.db.as_uri() + "?mode=rw&uri=true"
            self.assertEqual((await check_sqlite_integrity(url, result_path=self.result))["status"], "ok")
        finally:
            await engine.dispose()

    async def test_persistence_failure_logs_and_preserves_previous_file(self):
        self.result.write_text('{"previous": true}')
        with patch("beaverhabits.integrity.os.replace", side_effect=OSError("private path")), patch("beaverhabits.integrity.logger") as logger:
            result = await check_sqlite_integrity(self.url, result_path=self.result)
        self.assertFalse(result["persisted"])
        self.assertEqual(json.loads(self.result.read_text()), {"previous": True})
        self.assertEqual(set(self.root.iterdir()), {self.db, self.result})
        logger.error.assert_called()
        self.assertNotIn("private path", str(logger.mock_calls))

    async def test_result_cannot_overwrite_database(self):
        before = self.db.read_bytes()
        with self.assertRaises(ValueError):
            await check_sqlite_integrity(self.url, result_path=self.db)
        self.assertEqual(self.db.read_bytes(), before)

    async def test_default_result_path_and_wal_database(self):
        connection = sqlite3.connect(self.db)
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("INSERT INTO fixture VALUES ('wal fixture')")
            connection.commit()
            result = await check_sqlite_integrity(self.url)
            self.assertEqual(result["status"], "ok")
            path = self.db.with_name(self.db.name + ".integrity.json")
            self.assertEqual(json.loads(path.read_text()), result)
        finally:
            connection.close()

    async def test_inflight_cancellation_joins_worker(self):
        entered = threading.Event()
        finished = threading.Event()
        def check(url, result_path, cancelled):
            entered.set()
            cancelled.wait(2)
            finished.set()
            return {"status": "error"}
        with patch("beaverhabits.integrity._check", side_effect=check):
            task = asyncio.create_task(check_sqlite_integrity(self.url, result_path=self.result))
            self.assertTrue(await asyncio.to_thread(entered.wait, 2))
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(task, 2)
            self.assertTrue(finished.is_set())
            self.assertFalse(self.result.exists())

    async def test_loop_recovers_after_unexpected_failure(self):
        second_run = asyncio.Event()
        count = 0
        async def check(*args, **kwargs):
            nonlocal count
            count += 1
            if count == 1:
                raise RuntimeError("private connection information")
            second_run.set()
            return {"status": "ok"}
        with patch("beaverhabits.integrity.check_sqlite_integrity", side_effect=check), patch("beaverhabits.integrity.logger") as logger:
            task = asyncio.create_task(daily_integrity_task(self.url, interval_seconds=0.001))
            await asyncio.wait_for(second_run.wait(), 2)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            logger.error.assert_called()
            self.assertNotIn("private", str(logger.mock_calls))

    async def test_loop_runs_without_backup_and_cancels(self):
        ran = asyncio.Event()
        async def check(*args, **kwargs):
            ran.set()
            return {"status": "ok"}
        with patch("beaverhabits.integrity.check_sqlite_integrity", side_effect=check):
            task = asyncio.create_task(daily_integrity_task(self.url, result_path=self.result, interval_seconds=86400))
            await asyncio.wait_for(ran.wait(), 1)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        for interval in [0, -1, float("nan"), float("inf"), True]:
            with self.assertRaises(ValueError):
                await daily_integrity_task(self.url, interval_seconds=interval)
