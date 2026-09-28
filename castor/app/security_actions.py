"""Fresh-password sensitive actions shared by the API and GUI.

Callers supply the authenticated account ID and the version captured at session
validation (never a newly fetched version to upgrade a stale login). No function
issues a replacement token. Passwordless users must first establish a password
via verified email recovery; a nonempty/random hash does not prove usability.
"""
from datetime import datetime, timezone
from uuid import UUID
import asyncio
import secrets

from fastapi_users.db import SQLAlchemyUserDatabase
from fastapi_users.exceptions import InvalidPasswordException
from sqlalchemy import delete, select, update
from sqlalchemy.exc import SQLAlchemyError

from castor.app import audit, db, rate_limits
from castor.app.users import UserManager

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
                    updated_at=datetime.now(timezone.utc).replace(tzinfo=None),
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


async def delete_account(user_id, expected_version, current_password) -> None:
    """Irreversibly erase an account in ONE transaction after a fresh-password step-up.

    Fixes audit F18 + F24.

    F18: the caller-supplied account id is never trusted for authorization. The
    password is verified against the hash of the account identified by
    ``user_id`` — the same principal the session token resolved to — and the
    mutation re-checks that identity, active state, version and hash inside the
    deleting transaction. A second, attacker-writable channel (the non-httpOnly
    ``castor_user`` cookie) can no longer select whose account is destroyed.

    F24: every row removal and the tombstone update share a single
    transaction, so a crash can no longer leave "personal data gone, account
    still active". WebAuthn credentials, recovery challenges, reset codes and
    circle rows are removed explicitly rather than relying on FK cascades,
    which SQLite does not enforce unless ``foreign_keys=ON`` is set on the
    connection.
    """
    user = await _authorize(user_id, expected_version, current_password)
    # Evict the in-memory habit list BEFORE the row disappears, or a pending
    # debounced flush can resurrect the JSON blob we are about to erase.
    from castor.storage import get_user_dict_storage

    await get_user_dict_storage().delete_user_habit_list(user)
    archived_email = f"deleted+{user.id}@deleted.invalid"
    try:
        async with db.async_session_maker() as session:
            manager = UserManager(SQLAlchemyUserDatabase(session, db.User))
            random_hash = await asyncio.to_thread(
                manager.password_helper.hash, secrets.token_urlsafe(48)
            )
            async with session.begin():
                # Obtain a row/write lock on the still-matching user, held until
                # the erasure commits. Serializes against password change and
                # passkey removal on SQLite and PostgreSQL.
                locked = await session.execute(
                    update(db.User).where(*_guard(user)).values(
                        token_version=db.User.token_version,
                    ).execution_options(synchronize_session=False)
                )
                if locked.rowcount != 1:
                    raise StaleAuthorizationError()

                for model in (
                    db.HabitListModel,
                    db.UserConfigsModel,
                    db.UserNoteImageModel,
                    db.UserApiTokenModel,
                    db.WebAuthnCredential,
                ):
                    await session.execute(
                        delete(model)
                        .where(model.user_id == user_id)
                        .execution_options(synchronize_session=False)
                    )
                # Recovery/reset rows carry no ORM relationship to the user.
                await session.execute(
                    delete(db.RecoveryEmailChallenge).where(
                        db.RecoveryEmailChallenge.user_id == user_id
                    )
                )
                await session.execute(
                    delete(db.PasswordResetCode).where(
                        db.PasswordResetCode.user_id == user_id
                    )
                )
                # Identity rows are keyed by email, not user id.
                await session.execute(
                    delete(db.UserIdentityModel).where(
                        db.UserIdentityModel.email == user.email
                    )
                )

                # Circles hold personal data (member lists, shared habit ids,
                # invited addresses). Remove the user's memberships, shared
                # habits and minted invites, then any circle they own together
                # with its contents. Written out per table rather than looped:
                # each model names a different owner column.
                from castor.app.circles import Circle, CircleHabit, CircleInvite, CircleMember

                owned_circle_ids = list(
                    (
                        await session.execute(
                            select(Circle.id).where(Circle.owner_id == user_id)
                        )
                    )
                    .scalars()
                    .all()
                )
                await session.execute(
                    delete(CircleMember).where(CircleMember.user_id == user_id)
                )
                await session.execute(
                    delete(CircleHabit).where(CircleHabit.owner_user_id == user_id)
                )
                await session.execute(
                    delete(CircleInvite).where(CircleInvite.invited_by == user_id)
                )
                for circle_id in owned_circle_ids:
                    await session.execute(
                        delete(CircleMember).where(CircleMember.circle_id == circle_id)
                    )
                    await session.execute(
                        delete(CircleHabit).where(CircleHabit.circle_id == circle_id)
                    )
                    await session.execute(
                        delete(CircleInvite).where(CircleInvite.circle_id == circle_id)
                    )
                    await session.execute(
                        delete(Circle).where(Circle.id == circle_id)
                    )

                # Tombstone last, in the same transaction. Bumping token_version
                # retires every outstanding JWT for the account.
                archived = await session.execute(
                    update(db.User).where(*_guard(user)).values(
                        email=archived_email,
                        hashed_password=random_hash,
                        is_active=False,
                        is_superuser=False,
                        is_verified=False,
                        token_version=db.User.token_version + 1,
                        updated_at=datetime.now(timezone.utc).replace(tzinfo=None),
                    ).execution_options(synchronize_session=False)
                )
                if archived.rowcount != 1:
                    raise StaleAuthorizationError()
    except SecurityActionError:
        # Policy/status errors (e.g. rowcount CAS rejection) propagate
        # unchanged; they are not DB failures and must never surface as 503.
        raise
    except SQLAlchemyError:
        raise SecurityActionUnavailable() from None
    await audit.record('account_delete', user_id=user_id)


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
