"""Disposable SQLite defensive tests; run this module in a separate process."""
import asyncio
import os
import secrets
import tempfile
import unittest
import uuid
from unittest.mock import AsyncMock, patch

_TMP = tempfile.TemporaryDirectory()
os.environ['DATABASE_URL'] = f'sqlite+aiosqlite:///{_TMP.name}/unused.db'
os.environ['JWT_SECRET'] = secrets.token_urlsafe(32)
os.environ['RESET_PASSWORD_TOKEN_SECRET'] = secrets.token_urlsafe(32)
os.environ['NICEGUI_STORAGE_SECRET'] = secrets.token_urlsafe(32)

from fastapi_users.db import SQLAlchemyUserDatabase
from pydantic import ValidationError
from sqlalchemy import select, update, func
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from beaverhabits.app import db, rate_limits, audit
from beaverhabits.app.schemas import UserUpdate
from beaverhabits.app.users import UserManager, VersionedJWTStrategy

from beaverhabits.app import security_actions as actions

PASSWORD = 'fixture original passphrase'
NEW_PASSWORD = 'fixture replacement passphrase'


class PublicSchemaTests(unittest.TestCase):
    def test_public_updates_reject_password_and_email_even_null(self):
        for payload in ({'password': PASSWORD}, {'password': None},
                        {'email': 'replacement@example.com'}, {'email': None}):
            with self.subTest(field=next(iter(payload)), null=next(iter(payload.values())) is None):
                with self.assertRaises(ValidationError):
                    UserUpdate.model_validate(payload)

    def test_non_sensitive_updates_remain_available(self):
        self.assertEqual(UserUpdate(is_active=False).model_dump(exclude_unset=True), {'is_active': False})


class SensitiveActionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.engine = create_async_engine(f'sqlite+aiosqlite:///{self.tmp.name}/fixture.db')
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as conn:
            await conn.run_sync(db.Base.metadata.create_all)
        self.factory_patch = patch.object(db, 'async_session_maker', self.sessions)
        self.factory_patch.start()
        async with self.sessions() as session:
            manager = UserManager(SQLAlchemyUserDatabase(session, db.User))
            self.helper = manager.password_helper
            self.user = db.User(id=uuid.uuid4(), email='fixture@example.com',
                                hashed_password=self.helper.hash(PASSWORD), token_version=0,
                                is_active=True, is_verified=True, is_superuser=False)
            self.other = db.User(id=uuid.uuid4(), email='other@example.com',
                                 hashed_password=self.helper.hash(PASSWORD), token_version=0,
                                 is_active=True, is_verified=True, is_superuser=False)
            session.add_all([self.user, self.other])
            session.add_all([db.WebAuthnCredential(id=b'fixture-key', user_id=self.user.id,
                                                  public_key=b'fixture-not-an-authenticator'),
                             db.WebAuthnCredential(id=b'other-key', user_id=self.other.id,
                                                  public_key=b'fixture-not-an-authenticator')])
            await session.commit()

    async def asyncTearDown(self):
        self.factory_patch.stop()
        await self.engine.dispose()
        self.tmp.cleanup()

    async def fresh(self):
        async with self.sessions() as session:
            return await session.get(db.User, self.user.id)

    async def key_exists(self):
        async with self.sessions() as session:
            return await session.get(db.WebAuthnCredential, b'fixture-key') is not None

    async def mutate(self, **values):
        async with self.sessions() as session:
            await session.execute(update(db.User).where(db.User.id == self.user.id).values(**values))
            await session.commit()

    async def test_registered_users_route_rejects_session_only_sensitive_fields(self):
        import httpx
        from fastapi import FastAPI
        from beaverhabits.app.schemas import UserRead
        from beaverhabits.app.users import fastapi_users, get_user_manager, get_jwt_strategy

        app = FastAPI()
        app.include_router(fastapi_users.get_users_router(UserRead, UserUpdate), prefix='/users')
        async def fixture_manager():
            async with self.sessions() as session:
                yield UserManager(SQLAlchemyUserDatabase(session, db.User))
        app.dependency_overrides[get_user_manager] = fixture_manager
        token = await get_jwt_strategy().write_token(self.user)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://fixture') as client:
            headers = {'Authorization': f'Bearer {token}'}
            for payload in ({'password': NEW_PASSWORD}, {'email': 'new@example.com'},
                            {'password': None}, {'email': None}):
                response = await client.patch('/users/me', headers=headers, json=payload)
                self.assertEqual(response.status_code, 422)
            self.assertEqual((await client.get('/users/me', headers=headers)).status_code, 200)
        fresh = await self.fresh()
        self.assertEqual(fresh.hashed_password, self.user.hashed_password)
        self.assertEqual(fresh.email, self.user.email)
        self.assertEqual(fresh.token_version, 0)

    async def test_password_change_uses_policy_and_revokes_old_session(self):
        strategy = VersionedJWTStrategy(secret=secrets.token_urlsafe(32), lifetime_seconds=3600)
        token = await strategy.write_token(self.user)
        updated = await actions.change_password(self.user.id, 0, PASSWORD, NEW_PASSWORD)
        self.assertIsInstance(updated, db.User)
        self.assertEqual(updated.token_version, 1)
        fresh = await self.fresh()
        self.assertTrue(self.helper.verify_and_update(NEW_PASSWORD, fresh.hashed_password)[0])
        self.assertFalse(self.helper.verify_and_update(PASSWORD, fresh.hashed_password)[0])
        async with self.sessions() as session:
            manager = UserManager(SQLAlchemyUserDatabase(session, db.User))
            self.assertIsNone(await strategy.read_token(token, manager))
            self.assertIsNotNone(await strategy.read_token(await strategy.write_token(fresh), manager))
            self.assertEqual(await session.scalar(select(func.count()).select_from(audit.AuditEvent)
                             .where(audit.AuditEvent.event == 'password_change')), 1)

    async def test_policy_failure_has_no_mutation(self):
        with self.assertRaises(actions.PasswordPolicyError):
            await actions.change_password(self.user.id, 0, PASSWORD, 'short')
        self.assertEqual((await self.fresh()).hashed_password, self.user.hashed_password)

    async def test_central_manager_policy_is_called(self):
        from fastapi_users.exceptions import InvalidPasswordException
        with patch.object(UserManager, 'validate_password', AsyncMock(side_effect=InvalidPasswordException('fixture policy'))):
            with self.assertRaises(actions.PasswordPolicyError):
                await actions.change_password(self.user.id, 0, PASSWORD, NEW_PASSWORD)
        self.assertEqual((await self.fresh()).token_version, 0)

    async def test_wrong_password_denies_both_mutations(self):
        with self.assertRaises(actions.AuthorizationError):
            await actions.change_password(self.user.id, 0, 'wrong', NEW_PASSWORD)
        with self.assertRaises(actions.AuthorizationError):
            await actions.remove_passkey(self.user.id, 0, 'wrong', b'fixture-key')
        self.assertTrue(await self.key_exists())
        self.assertEqual((await self.fresh()).token_version, 0)

    async def test_inactive_and_missing_accounts_are_denied(self):
        await self.mutate(is_active=False)
        for user_id in (self.user.id, uuid.uuid4()):
            with self.assertRaises(actions.AuthorizationError):
                await actions.change_password(user_id, 0, PASSWORD, NEW_PASSWORD)
            with self.assertRaises(actions.AuthorizationError):
                await actions.remove_passkey(user_id, 0, PASSWORD, b'fixture-key')
        self.assertTrue(await self.key_exists())

    async def test_passwordless_empty_and_malformed_hashes_do_not_authorize(self):
        for value in ('', 'not-a-password-hash', self.helper.hash('')):
            await self.mutate(hashed_password=value)
            with self.assertRaises(actions.AuthorizationError):
                await actions.remove_passkey(self.user.id, 0, '', b'fixture-key')
            with self.assertRaises(actions.AuthorizationError):
                await actions.remove_passkey(self.user.id, 0, PASSWORD, b'fixture-key')
        self.assertTrue(await self.key_exists())

    async def test_stale_session_rejected_even_with_current_password(self):
        await self.mutate(token_version=1)
        with self.assertRaises(actions.StaleAuthorizationError):
            await actions.change_password(self.user.id, 0, PASSWORD, NEW_PASSWORD)
        with self.assertRaises(actions.StaleAuthorizationError):
            await actions.remove_passkey(self.user.id, 0, PASSWORD, b'fixture-key')
        self.assertTrue(await self.key_exists())

    async def test_invalid_version_types_rejected(self):
        for version in (None, True, '0', -1):
            with self.assertRaises(actions.AuthorizationError):
                await actions.remove_passkey(self.user.id, version, PASSWORD, b'fixture-key')

    async def test_last_key_removal_requires_proven_password_and_preserves_session(self):
        self.assertTrue(await actions.remove_passkey(self.user.id, 0, PASSWORD, b'fixture-key'))
        self.assertFalse(await self.key_exists())
        self.assertEqual((await self.fresh()).token_version, 0)

    async def test_foreign_and_missing_credentials_leave_account_unchanged(self):
        for credential_id in (b'other-key', b'absent-key'):
            with self.assertRaises(actions.CredentialNotFoundError):
                await actions.remove_passkey(self.user.id, 0, PASSWORD, credential_id)
        self.assertTrue(await self.key_exists())
        self.assertEqual((await self.fresh()).token_version, 0)

    async def test_rate_limit_is_shared_across_actions_and_fails_closed(self):
        for _ in range(actions.STEP_UP_LIMIT):
            with self.assertRaises(actions.AuthorizationError):
                await actions.change_password(self.user.id, 0, 'wrong', NEW_PASSWORD)
        with self.assertRaises(actions.RateLimitError):
            await actions.remove_passkey(self.user.id, 0, PASSWORD, b'fixture-key')
        with patch.object(rate_limits, 'consume', AsyncMock(side_effect=RuntimeError('private details'))):
            with self.assertRaises(actions.SecurityActionUnavailable) as error:
                await actions.change_password(self.user.id, 0, PASSWORD, NEW_PASSWORD)
            self.assertNotIn('private details', str(error.exception))
        self.assertTrue(await self.key_exists())

    async def test_password_change_cas_rejects_mutation_after_authorization(self):
        original = actions._authorize
        async def raced(*args):
            user = await original(*args)
            await self.mutate(token_version=1)
            return user
        with patch.object(actions, '_authorize', raced):
            with self.assertRaises(actions.StaleAuthorizationError):
                await actions.change_password(self.user.id, 0, PASSWORD, NEW_PASSWORD)
        self.assertEqual((await self.fresh()).hashed_password, self.user.hashed_password)

    async def test_deletion_cas_rejects_password_removal_without_version_bump(self):
        original = actions._authorize
        async def raced(*args):
            user = await original(*args)
            await self.mutate(hashed_password='')
            return user
        with patch.object(actions, '_authorize', raced):
            with self.assertRaises(actions.StaleAuthorizationError):
                await actions.remove_passkey(self.user.id, 0, PASSWORD, b'fixture-key')
        self.assertTrue(await self.key_exists())

    async def test_transaction_policy_errors_propagate_not_503(self):
        # P1-4: SecurityActionError subclasses raised INSIDE the write
        # transaction must reach callers unchanged — never be converted to
        # SecurityActionUnavailable (503). A 503 here would tell a user with an
        # expired session "server error" instead of "sign in again".
        for error in (actions.StaleAuthorizationError(), actions.PasswordPolicyError()):
            with self.subTest(error=type(error).__name__):
                with self.assertRaises(type(error)):
                    await self._force_transaction_error(error)
                self.assertEqual((await self.fresh()).token_version, 0)

    async def _force_transaction_error(self, error):
        # Drive change_password's guarded UPDATE to raise `error` inside
        # session.begin(), after _authorize approved the snapshot.
        from sqlalchemy.ext.asyncio import AsyncSession
        from sqlalchemy.sql.dml import Update
        original = AsyncSession.execute
        async def raising(session, statement, *args, **kwargs):
            if isinstance(statement, Update) and statement.table.name == 'user':
                raise error
            return await original(session, statement, *args, **kwargs)
        with patch.object(AsyncSession, 'execute', raising):
            return await actions.change_password(self.user.id, 0, PASSWORD, NEW_PASSWORD)

    async def test_deactivation_after_authorization_blocks_both_actions(self):
        original = actions._authorize
        async def raced(*args):
            user = await original(*args)
            await self.mutate(is_active=False)
            return user
        with patch.object(actions, '_authorize', raced):
            with self.assertRaises(actions.StaleAuthorizationError):
                await actions.change_password(self.user.id, 0, PASSWORD, NEW_PASSWORD)
            await self.mutate(is_active=True)
            with self.assertRaises(actions.StaleAuthorizationError):
                await actions.remove_passkey(self.user.id, 0, PASSWORD, b'fixture-key')
        self.assertTrue(await self.key_exists())
        self.assertEqual((await self.fresh()).hashed_password, self.user.hashed_password)

    async def test_deletion_database_error_rolls_back_and_is_public_safe(self):
        from sqlalchemy.exc import OperationalError
        from sqlalchemy.ext.asyncio import AsyncSession
        from sqlalchemy.sql.dml import Delete
        original = AsyncSession.execute
        async def failing(session, statement, *args, **kwargs):
            if isinstance(statement, Delete) and statement.table.name == 'webauthn_credential':
                raise OperationalError('private statement', {}, Exception('private database details'))
            return await original(session, statement, *args, **kwargs)
        with patch.object(AsyncSession, 'execute', failing):
            with self.assertRaises(actions.SecurityActionUnavailable) as error:
                await actions.remove_passkey(self.user.id, 0, PASSWORD, b'fixture-key')
        self.assertNotIn('private', str(error.exception))
        self.assertTrue(await self.key_exists())
        self.assertEqual((await self.fresh()).token_version, 0)

    async def test_concurrent_password_changes_have_one_winner(self):
        original = actions._authorize
        ready = asyncio.Event()
        arrivals = 0
        async def synchronize(*args):
            nonlocal arrivals
            user = await original(*args)
            arrivals += 1
            if arrivals == 2:
                ready.set()
            await asyncio.wait_for(ready.wait(), 10)
            return user
        from sqlalchemy.exc import OperationalError
        with patch.object(actions, '_authorize', synchronize):
            results = await asyncio.gather(
                actions.change_password(self.user.id, 0, PASSWORD, NEW_PASSWORD),
                actions.change_password(self.user.id, 0, PASSWORD, NEW_PASSWORD), return_exceptions=True)
        self.assertEqual(sum(isinstance(result, db.User) for result in results), 1)
        # Both outcomes assert exactly one winner:
        # - StaleAuthorizationError: the CAS guard rejected the loser inside
        #   the write transaction.
        # - OperationalError (SQLITE_BUSY): SQLite serialized the writers and
        #   the loser could not even begin its transaction. Under load or
        #   different SQLite builds the loser arrives here instead; that is a
        #   correct one-winner result, not a product bug.
        losers = [r for r in results if not isinstance(r, db.User)]
        self.assertEqual(len(losers), 1)
        self.assertIsInstance(losers[0], (actions.StaleAuthorizationError, OperationalError))
        self.assertEqual((await self.fresh()).token_version, 1)


if __name__ == '__main__':
    unittest.main()
