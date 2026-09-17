"""Offline legacy-link recovery invariants: temporary DB and captured SMTP only."""
import asyncio
import datetime as dt
import os
import secrets
import tempfile
import threading
import unittest
from unittest.mock import AsyncMock, patch
from uuid import uuid4

_BOOT = tempfile.TemporaryDirectory()
os.environ['DATABASE_URL'] = f'sqlite+aiosqlite:///{_BOOT.name}/unused.db'
for _name in ('JWT_SECRET', 'RESET_PASSWORD_TOKEN_SECRET', 'NICEGUI_STORAGE_SECRET'):
    os.environ[_name] = secrets.token_urlsafe(32)
os.environ['TRUSTED_LOCAL_EMAIL'] = ''
os.environ['TRUSTED_EMAIL_HEADER'] = ''
os.environ['SENTRY_DSN'] = ''
import dotenv
with patch.object(dotenv, 'load_dotenv', return_value=False):
    import beaverhabits.main

from fastapi_users import exceptions
from fastapi_users.jwt import decode_jwt, generate_jwt
from fastapi_users.manager import RESET_PASSWORD_TOKEN_AUDIENCE
from sqlalchemy import event, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from beaverhabits import utils, views
from beaverhabits.app import auth, db, rate_limits
from beaverhabits.app.users import UserManager
from beaverhabits.configs import settings

PASSWORD = 'replacement fixture passphrase'


class LegacyRecoverySafetyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.url = f'sqlite+aiosqlite:///{self.tmp.name}/recovery.db'
        self.engine = create_async_engine(self.url)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        self.mail, self.notices, self.logs = [], [], []
        self.main_thread = threading.get_ident()
        def capture_mail(*args, **kwargs):
            self.mail.append((args, kwargs, threading.get_ident()))
        self.patches = [
            patch.object(db, 'engine', self.engine),
            patch.object(db, 'async_session_maker', self.sessions),
            patch.object(rate_limits, 'async_session_maker', self.sessions),
            patch.object(views, 'send_email', side_effect=capture_mail),
            patch.object(views.ui, 'notify', side_effect=lambda *a, **kw: self.notices.append((a, kw))),
            patch('beaverhabits.app.audit.record', new_callable=AsyncMock),
        ]
        for level in ('debug', 'info', 'warning', 'error', 'exception'):
            self.patches.append(patch.object(views.logger, level, side_effect=lambda *a, **kw: self.logs.append((a, kw))))
        for p in self.patches:
            p.start()
        await db.create_db_and_tables()
        self.user = db.User(id=uuid4(), email='fixture@example.com', hashed_password='unused', token_version=4, is_active=True)
        async with self.sessions.begin() as session:
            session.add(self.user)

    async def asyncTearDown(self):
        for p in reversed(self.patches):
            p.stop()
        await self.engine.dispose()
        self.tmp.cleanup()

    async def fresh(self):
        async with self.sessions() as session:
            return await session.get(db.User, self.user.id)

    async def verified(self):
        return await auth.user_from_reset_token(auth.user_create_reset_token(self.user))

    async def test_existing_gui_decorator_is_not_cross_worker_admission(self):
        # Each worker gets its own cache; differently cased accounts have distinct keys.
        # A HTTP-only /auth/ middleware does not cover NiceGUI websocket callbacks.
        calls = []
        async def request(email):
            calls.append(email)
        worker1 = utils.ratelimiter(1, 60)(request)
        worker2 = utils.ratelimiter(1, 60)(request)
        await worker1('fixture@example.com')
        await worker2('fixture@example.com')
        await worker1('FIXTURE@example.com')
        self.assertEqual(len(calls), 3)

    async def test_recipient_independent_notice_and_threaded_mail(self):
        await views.forgot_password(self.user.email)
        await views.forgot_password('absent@example.com')
        async with self.sessions.begin() as session:
            await session.execute(update(db.User).where(db.User.id == self.user.id).values(is_active=False))
        await views.forgot_password(self.user.email)
        self.assertEqual(len(self.mail), 1)
        self.assertNotEqual(self.mail[0][2], self.main_thread)
        self.assertTrue(all(notice == self.notices[0] for notice in self.notices))
        self.assertNotIn(self.user.email, str(self.notices))

    async def test_throttle_persists_across_engine_and_normalizes_account(self):
        await views.forgot_password(self.user.email)
        second = create_async_engine(self.url)
        try:
            with patch.object(rate_limits, 'async_session_maker', async_sessionmaker(second, expire_on_commit=False)):
                await views.forgot_password('  FIXTURE@EXAMPLE.COM  ')
        finally:
            await second.dispose()
        self.assertEqual(len(self.mail), 1)
        self.assertEqual(self.notices[0], self.notices[1])

    async def test_email_failure_is_generic_and_never_logs_exception(self):
        with patch.object(views, 'send_email', side_effect=RuntimeError('synthetic-private-mail-body')):
            await views.forgot_password(self.user.email)
        await views.forgot_password('absent@example.com')
        self.assertEqual(self.notices[0], self.notices[1])
        self.assertNotIn('synthetic-private-mail-body', str(self.logs))
        self.assertTrue(self.logs)
        self.assertNotIn(self.user.email, str(self.logs))

    async def test_smtp_socket_timeout_is_bounded(self):
        with patch.object(utils.smtplib, 'SMTP_SSL') as smtp:
            utils.send_email('fixture subject', 'fixture body', ['fixture@example.com'])
        timeout = smtp.call_args.kwargs.get('timeout')
        self.assertIsNotNone(timeout)
        self.assertTrue(0 < timeout <= 30)
        smtp.return_value.__enter__.return_value.sendmail.assert_called_once()

    async def test_zero_lifetime_has_expiry_and_no_expiry_token_rejected(self):
        with patch.object(settings, 'RESET_PASSWORD_TOKEN_LIFETIME_SECONDS', 0):
            token = auth.user_create_reset_token(self.user)
        data = decode_jwt(token, settings.RESET_PASSWORD_TOKEN_SECRET, [RESET_PASSWORD_TOKEN_AUDIENCE])
        self.assertIn('exp', data)
        self.assertTrue(0 < data['exp'] - dt.datetime.now(dt.timezone.utc).timestamp() <= 3600)
        data.pop('exp')
        unbounded = generate_jwt(data, settings.RESET_PASSWORD_TOKEN_SECRET, None)
        with self.assertRaises(exceptions.InvalidResetPasswordToken):
            await auth.user_from_reset_token(unbounded)

    async def test_reset_policy_precedes_hashing(self):
        user = await self.verified()
        with patch.object(UserManager, 'validate_password', AsyncMock(side_effect=exceptions.InvalidPasswordException('fixture policy'))) as policy, \
             patch.object(UserManager(None).password_helper.__class__, 'hash', side_effect=AssertionError('Must not hash rejected passwords')):
            with self.assertRaises(exceptions.InvalidPasswordException):
                await auth.user_reset_password(user, PASSWORD)
        policy.assert_awaited_once()
        self.assertEqual((await self.fresh()).token_version, 4)

    async def test_reset_commits_once_without_manager_update_and_revokes_sibling(self):
        user = await self.verified()
        sibling = await self.verified()
        threads = []
        helper_type = UserManager(None).password_helper.__class__
        real_hash = helper_type.hash
        def tracked_hash(helper, password):
            threads.append(threading.get_ident())
            return real_hash(helper, password)
        with patch.object(UserManager, '_update', side_effect=AssertionError('Independent commit forbidden')), \
             patch.object(helper_type, 'hash', tracked_hash):
            updated = await auth.user_reset_password(user, PASSWORD)
        self.assertEqual(updated.token_version, 5)
        self.assertTrue(UserManager(None).password_helper.verify_and_update(PASSWORD, (await self.fresh()).hashed_password)[0])
        self.assertNotEqual(threads[0], self.main_thread)
        with self.assertRaises(exceptions.InvalidResetPasswordToken):
            await auth.user_reset_password(sibling, PASSWORD)
        self.assertEqual((await self.fresh()).token_version, 5)

    async def test_stale_active_hash_or_version_cannot_overwrite(self):
        for mutation in ({'is_active': False}, {'hashed_password': 'other-hash'}, {'token_version': 5}):
            async with self.sessions.begin() as session:
                await session.execute(update(db.User).where(db.User.id == self.user.id).values(is_active=True, hashed_password='unused', token_version=4))
            user = await self.verified()
            async with self.sessions.begin() as session:
                await session.execute(update(db.User).where(db.User.id == self.user.id).values(**mutation))
            with self.assertRaises(exceptions.InvalidResetPasswordToken):
                await auth.user_reset_password(user, PASSWORD)
            fresh = await self.fresh()
            for key, value in mutation.items():
                self.assertEqual(getattr(fresh, key), value)

    async def test_state_change_while_hashing_fails_cas(self):
        user = await self.verified()
        real_to_thread = asyncio.to_thread
        async def hashing_race(fn, *args, **kwargs):
            result = await real_to_thread(fn, *args, **kwargs)
            async with self.sessions.begin() as session:
                await session.execute(update(db.User).where(db.User.id == self.user.id).values(token_version=5))
            return result
        with patch.object(auth.asyncio, 'to_thread', side_effect=hashing_race):
            with self.assertRaises(exceptions.InvalidResetPasswordToken):
                await auth.user_reset_password(user, PASSWORD)
        self.assertEqual((await self.fresh()).hashed_password, 'unused')

    async def test_concurrent_verified_tabs_only_one_success(self):
        tabs = [await self.verified(), await self.verified()]
        results = await asyncio.gather(*(auth.user_reset_password(tab, PASSWORD) for tab in tabs), return_exceptions=True)
        self.assertEqual(sum(isinstance(result, db.User) for result in results), 1)
        self.assertEqual(sum(isinstance(result, exceptions.InvalidResetPasswordToken) for result in results), 1)
        self.assertEqual((await self.fresh()).token_version, 5)

    async def test_expiry_after_page_load_rejected(self):
        user = await self.verified()
        user._reset_token_expires_at = dt.datetime.now(dt.timezone.utc).timestamp() - 1
        with self.assertRaises(exceptions.InvalidResetPasswordToken):
            await auth.user_reset_password(user, PASSWORD)
        self.assertEqual((await self.fresh()).token_version, 4)

    async def test_gui_failures_do_not_sign_in_or_redirect(self):
        for error in (exceptions.InvalidResetPasswordToken(), exceptions.UserInactive(), exceptions.InvalidPasswordException('fixture unsafe detail')):
            with patch.object(views, 'user_reset_password', AsyncMock(side_effect=error)), \
                 patch.object(views, 'login_user', AsyncMock()) as login, \
                 patch.object(views, 'redirect') as redirect:
                await views.reset_password(self.user, PASSWORD)
                login.assert_not_awaited()
                redirect.assert_not_called()
        self.assertEqual(len(self.notices), 3)
        self.assertNotIn('fixture unsafe detail', str(self.notices))

    async def test_throttle_store_failure_suppresses_mail_and_keeps_notice(self):
        with patch.object(rate_limits, 'consume', AsyncMock(side_effect=RuntimeError('synthetic storage detail'))):
            await views.forgot_password(self.user.email)
        await views.forgot_password('absent@example.com')
        self.assertEqual(len(self.mail), 0)
        self.assertEqual(self.notices[0], self.notices[1])
        self.assertNotIn('synthetic storage detail', str(self.logs))

    async def test_commit_failure_rolls_back_and_gui_does_not_sign_in(self):
        user = await self.verified()
        def fail_commit(connection):
            raise RuntimeError('synthetic commit failure')
        event.listen(self.engine.sync_engine, 'commit', fail_commit)
        try:
            with patch.object(views, 'login_user', AsyncMock()) as login, patch.object(views, 'redirect') as redirect:
                await views.reset_password(user, PASSWORD)
                login.assert_not_awaited()
                redirect.assert_not_called()
        finally:
            event.remove(self.engine.sync_engine, 'commit', fail_commit)
        fresh = await self.fresh()
        self.assertEqual((fresh.hashed_password, fresh.token_version), ('unused', 4))
        self.assertNotIn('synthetic commit failure', str(self.logs))
        self.assertIn('could not confirm', str(self.notices))

    async def test_verified_version_snapshot_not_refreshed_at_submission(self):
        user = await self.verified()
        async with self.sessions.begin() as session:
            await session.execute(update(db.User).where(db.User.id == self.user.id).values(token_version=5))
        user.token_version = 5
        with self.assertRaises(exceptions.InvalidResetPasswordToken):
            await auth.user_reset_password(user, PASSWORD)
        self.assertEqual((await self.fresh()).hashed_password, 'unused')

    async def test_gui_signs_in_only_committed_user(self):
        with patch.object(views, 'login_user', AsyncMock()) as login, patch.object(views, 'redirect') as redirect:
            await views.reset_password(await self.verified(), PASSWORD)
        self.assertEqual(login.await_args.args[0].token_version, 5)
        self.assertEqual((await self.fresh()).token_version, 5)
        redirect.assert_called_once()


if __name__ == '__main__':
    unittest.main()
