"""Fresh-password sensitive actions shared by the API and GUI.

Callers supply the authenticated account ID and the version captured at session
validation (never a newly fetched version to upgrade a stale login). No function
issues a replacement token. Passwordless users must first establish a password
via verified email recovery; a nonempty/random hash does not prove usability.
"""
from datetime import datetime, timezone
from uuid import UUID
import asyncio

from fastapi_users.db import SQLAlchemyUserDatabase
from fastapi_users.exceptions import InvalidPasswordException
from sqlalchemy import delete, select, update
from sqlalchemy.exc import SQLAlchemyError

from beaverhabits.app import audit, db, rate_limits
from beaverhabits.app.users import UserManager

STEP_UP_LIMIT = 10
STEP_UP_WINDOW = 600
MAX_PASSWORD_LENGTH = 4096


class SecurityActionError(Exception):
    """Fixed, public-safe message; never contains input or backend details."""
    status_code = 400
    message = 'Sensitive action could not be completed'

    def __init__(self):
        super().__init__(self.message)


class AuthorizationError(SecurityActionError):
    status_code = 401
    message = 'Fresh current-password authorization required; use verified email recovery if needed'


class StaleAuthorizationError(AuthorizationError):
    status_code = 409
    message = 'Account security changed; sign in again before retrying'


class PasswordPolicyError(SecurityActionError):
    message = 'New password does not meet the password policy'


class CredentialNotFoundError(SecurityActionError):
    status_code = 404
    message = 'Passkey not found'


class RateLimitError(SecurityActionError):
    status_code = 429
    message = 'Too many sensitive-action attempts; try again later'


class SecurityActionUnavailable(SecurityActionError):
    status_code = 503
    message = 'Security action temporarily unavailable'


async def _authorize(user_id, expected_version, current_password):
    """Consume a shared persistent budget before password work; read no cache.

    This snapshot is NOT itself mutation authorization. The writer must also
    match its hash, active state and version in the mutation transaction.
    """
    if not isinstance(user_id, UUID) or type(expected_version) is not int or expected_version < 0:
        raise AuthorizationError()
    try:
        allowed = await rate_limits.consume('sensitive-action', user_id, STEP_UP_LIMIT,
                                            STEP_UP_WINDOW, sessions=db.async_session_maker)
    except Exception:
        raise SecurityActionUnavailable() from None
    if not allowed:
        raise RateLimitError()
    if not isinstance(current_password, str) or not 0 < len(current_password) <= MAX_PASSWORD_LENGTH:
        raise AuthorizationError()
    try:
        async with db.async_session_maker() as session:
            user = (await session.execute(select(db.User).where(
                db.User.id == user_id, db.User.is_active.is_(True)
            ).execution_options(populate_existing=True))).scalar_one_or_none()
            if user is None:
                raise AuthorizationError()
            if user.token_version != expected_version:
                raise StaleAuthorizationError()
            if not user.hashed_password:
                raise AuthorizationError()
            manager = UserManager(SQLAlchemyUserDatabase(session, db.User))
            try:
                valid, _ = await asyncio.to_thread(manager.password_helper.verify_and_update,
                                                   current_password, user.hashed_password)
            except Exception:
                # Malformed/unsupported historical hashes fail closed. Do not
                # persist automatic rehashes outside the guarded transaction.
                raise AuthorizationError() from None
            if not valid:
                raise AuthorizationError()
            session.expunge(user)
            return user
    except SecurityActionError:
        # Policy/status errors propagate unchanged; the DB catch below must
        # never convert them into 503 SecurityActionUnavailable.
        raise
    except SQLAlchemyError:
        raise SecurityActionUnavailable() from None


def _guard(user):
    return (db.User.id == user.id, db.User.is_active.is_(True),
            db.User.token_version == user.token_version,
            db.User.hashed_password == user.hashed_password)


async def change_password(user_id, expected_version, current_password, new_password) -> db.User:
    """Return committed detached User; increment version once and mint no login.

    The manager's existing validate_password is the policy choke point, shared
    with registration and recovery. The service owns the write/commit instead
    of calling manager.update, which cannot compare a session's version.
    """
    user = await _authorize(user_id, expected_version, current_password)
    if not isinstance(new_password, str) or len(new_password) > MAX_PASSWORD_LENGTH:
        raise PasswordPolicyError()
    try:
        async with db.async_session_maker() as session:
            manager = UserManager(SQLAlchemyUserDatabase(session, db.User))
            try:
                await manager.validate_password(new_password, user)
            except InvalidPasswordException:
                raise PasswordPolicyError() from None
            hashed_password = await asyncio.to_thread(manager.password_helper.hash, new_password)
            async with session.begin():
                result = await session.execute(update(db.User).where(*_guard(user)).values(
                    hashed_password=hashed_password, token_version=db.User.token_version + 1,
                    updated_at=datetime.now(timezone.utc),
                ).execution_options(synchronize_session=False))
                if result.rowcount != 1:
                    raise StaleAuthorizationError()
                updated = (await session.execute(select(db.User).where(db.User.id == user_id))).scalar_one()
                session.expunge(updated)
    except SecurityActionError:
        # Policy/status errors (e.g. rowcount CAS rejection) propagate
        # unchanged; they are not DB failures and must never surface as 503.
        raise
    except SQLAlchemyError:
        raise SecurityActionUnavailable() from None
    await audit.record('password_change', user_id=user_id)
    return updated


async def remove_passkey(user_id, expected_version, current_password, credential_id) -> bool:
    """Atomically prove a usable password remains and delete an owned key.

    A conditional no-op user UPDATE obtains a row/write lock, held until the
    credential deletion commits. This serializes password removal/deactivation
    with the deletion on SQLite and PostgreSQL without revoking the session.
    No key count is needed: successful fresh password verification plus the
    locked matching hash proves another usable sign-in method remains.
    """
    user = await _authorize(user_id, expected_version, current_password)
    if not isinstance(credential_id, bytes) or not 0 < len(credential_id) <= 1024:
        raise CredentialNotFoundError()
    try:
        async with db.async_session_maker() as session:
            async with session.begin():
                locked = await session.execute(update(db.User).where(*_guard(user)).values(
                    token_version=db.User.token_version,
                ).execution_options(synchronize_session=False))
                if locked.rowcount != 1:
                    raise StaleAuthorizationError()
                deleted = await session.execute(delete(db.WebAuthnCredential).where(
                    db.WebAuthnCredential.id == credential_id,
                    db.WebAuthnCredential.user_id == user_id,
                ).execution_options(synchronize_session=False))
                if deleted.rowcount != 1:
                    raise CredentialNotFoundError()
    except SecurityActionError:
        # Policy/status errors (e.g. rowcount CAS rejection) propagate
        # unchanged; they are not DB failures and must never surface as 503.
        raise
    except SQLAlchemyError:
        raise SecurityActionUnavailable() from None
    await audit.record('passkey_delete', user_id=user_id)
    return True
