import asyncio
import contextlib
import datetime
import secrets
from typing import Optional
from uuid import UUID

import jwt
from fastapi.security import OAuth2PasswordRequestForm
from fastapi_users import exceptions, models
from fastapi_users.exceptions import UserAlreadyExists
from fastapi_users.jwt import decode_jwt, generate_jwt
from fastapi_users.manager import RESET_PASSWORD_TOKEN_AUDIENCE
from nicegui import app
from sqlalchemy import update

from beaverhabits.app.db import User, get_async_session, get_user_db
from beaverhabits.app.schemas import UserCreate
from beaverhabits.app.users import UserManager, get_jwt_strategy, get_user_manager
from beaverhabits.configs import settings
from beaverhabits.logger import logger

get_async_session_context = contextlib.asynccontextmanager(get_async_session)
get_user_db_context = contextlib.asynccontextmanager(get_user_db)
get_user_manager_context = contextlib.asynccontextmanager(get_user_manager)


async def user_authenticate(email: str, password: str) -> Optional[User]:
    try:
        assert email, "Email must be provided"
        assert password, "Password must be provided"

        async with get_async_session_context() as session:
            async with get_user_db_context(session) as user_db:
                async with get_user_manager_context(user_db) as user_manager:
                    # user_logout()
                    credentials = OAuth2PasswordRequestForm(
                        username=email, password=password
                    )
                    user = await user_manager.authenticate(credentials)
                    if user is None or not user.is_active:
                        return None
                    return user
    except Exception:
        logger.exception("Unkownn Exception")
        return None


async def user_create_token(user: User) -> Optional[str]:
    try:
        async with get_async_session_context() as session:
            async with get_user_db_context(session) as user_db:
                async with get_user_manager_context(user_db):
                    strategy = get_jwt_strategy()
                    token = await strategy.write_token(user)
                    if token is not None:
                        return token
                    else:
                        return None
    except Exception:
        return None


async def user_check_token(token: str | None) -> bool:
    try:
        async with get_async_session_context() as session:
            async with get_user_db_context(session) as user_db:
                async with get_user_manager_context(user_db) as user_manager:
                    if token is None:
                        return False
                    strategy = get_jwt_strategy()
                    user = await strategy.read_token(token, user_manager)
                    return bool(user and user.is_active)
    except Exception:
        return False


async def user_from_token(token: str | None) -> User | None:
    async with get_async_session_context() as session:
        async with get_user_db_context(session) as user_db:
            async with get_user_manager_context(user_db) as user_manager:
                if not token:
                    return None
                strategy = get_jwt_strategy()
                user = await strategy.read_token(token, user_manager)
                return user if user and user.is_active else None


async def user_create(
    email: str, password: str | None = None, is_superuser: bool = False
) -> User:
    try:
        async with get_async_session_context() as session:
            async with get_user_db_context(session) as user_db:
                async with get_user_manager_context(user_db) as user_manager:
                    user = await user_manager.create(
                        UserCreate(
                            email=email,
                            password=secrets.token_urlsafe(48) if password is None else password,
                            is_superuser=is_superuser,
                        )
                    )
                    return user
    except UserAlreadyExists:
        raise Exception("User already exists!")


async def user_get_by_email(email: str) -> Optional[User]:
    try:
        async with get_async_session_context() as session:
            async with get_user_db_context(session) as user_db:
                async with get_user_manager_context(user_db) as user_manager:
                    user = await user_manager.get_by_email(email)
                    return user
    except Exception:
        return None


async def user_get_by_id(user_id: UUID) -> User:
    async with get_async_session_context() as session:
        async with get_user_db_context(session) as user_db:
            async with get_user_manager_context(user_db) as user_manager:
                return await user_manager.get(user_id)


def user_logout() -> bool:
    app.storage.user.clear()
    app.storage.user["clear_auth_cookie"] = True
    return True


def user_create_reset_token(user: User) -> str:
    # Ref: https://github.com/fastapi-users/fastapi-users/blob/9d78b2a35dc7f35c2ffca67232c11f4d27a5db00/fastapi_users/manager.py#L358
    if not user.is_active:
        raise exceptions.UserInactive()

    token_data = {
        "sub": str(user.id),
        "aud": RESET_PASSWORD_TOKEN_AUDIENCE,
        "ver": user.token_version,
    }
    assert settings.RESET_PASSWORD_TOKEN_SECRET, "Missing JWT secret"
    token = generate_jwt(
        token_data,
        settings.RESET_PASSWORD_TOKEN_SECRET,
        settings.RESET_PASSWORD_TOKEN_LIFETIME_SECONDS if settings.RESET_PASSWORD_TOKEN_LIFETIME_SECONDS > 0 else 3600,
    )

    return token


async def user_from_reset_token(token: str) -> User:
    # ref: https://github.com/fastapi-users/fastapi-users/blob/9d78b2a35dc7f35c2ffca67232c11f4d27a5db00/fastapi_users/manager.py#L386
    try:
        data = decode_jwt(
            token,
            settings.RESET_PASSWORD_TOKEN_SECRET,
            [RESET_PASSWORD_TOKEN_AUDIENCE],
        )
    except jwt.PyJWTError:
        raise exceptions.InvalidResetPasswordToken()

    try:
        user_id = UUID(data["sub"])
        # Reject historic immortal links as well as newly expired links.
        if type(data.get("exp")) not in (int, float):
            raise ValueError()
        user = await user_get_by_id(user_id)
    except (KeyError, ValueError, TypeError, AttributeError, exceptions.UserNotExists):
        raise exceptions.InvalidResetPasswordToken() from None
    if not user.is_active:
        raise exceptions.UserInactive()
    if type(data.get("ver")) is not int or data["ver"] != user.token_version:
        raise exceptions.InvalidResetPasswordToken()

    # The GUI retains this detached user after page load. Preserve the verified
    # snapshot, never replace it with a freshly loaded version during submission.
    user._reset_token_version = data["ver"]
    user._reset_token_hash = user.hashed_password
    user._reset_token_expires_at = data["exp"]
    return user


async def user_reset_password(user: User, new_password: str) -> User:
    manager = UserManager(None)
    await manager.validate_password(new_password, user)
    expected_version = getattr(user, "_reset_token_version", user.token_version)
    expected_hash = getattr(user, "_reset_token_hash", user.hashed_password)
    expires_at = getattr(user, "_reset_token_expires_at", None)

    def check_snapshot():
        if not user.is_active or type(expected_version) is not int:
            raise exceptions.InvalidResetPasswordToken()
        if expires_at is not None and expires_at <= datetime.datetime.now(datetime.timezone.utc).timestamp():
            raise exceptions.InvalidResetPasswordToken()

    check_snapshot()
    hashed_password = await asyncio.to_thread(manager.password_helper.hash, new_password)
    async with get_async_session_context() as session:
        async with session.begin():
            check_snapshot()
            changed = await session.execute(
                update(User).where(
                    User.id == user.id,
                    User.is_active.is_(True),
                    User.hashed_password == expected_hash,
                    User.token_version == expected_version,
                ).values(hashed_password=hashed_password, token_version=User.token_version + 1)
                .execution_options(synchronize_session=False)
            )
            if changed.rowcount != 1:
                raise exceptions.InvalidResetPasswordToken()
            # Also cover expiry while waiting for the database write lock.
            check_snapshot()
            updated_user = await session.get(User, user.id)
        # The transaction committed successfully before returning a login candidate.
    from beaverhabits.app.audit import record
    await record("password_reset", user_id=user.id)
    return updated_user


async def user_deletion(user: User) -> None:
    async with get_async_session_context() as session:
        async with get_user_db_context(session) as user_db:
            async with get_user_manager_context(user_db) as user_manager:
                await user_manager.delete(user)


async def user_archive(user: User) -> User:
    """Disable and anonymize an account while retaining a non-personal tombstone."""
    archived_email = f"deleted+{user.id}@deleted.invalid"
    async with get_async_session_context() as session:
        async with get_user_db_context(session) as user_db:
            async with get_user_manager_context(user_db) as user_manager:
                return await user_manager._update(
                    user,
                    {
                        "email": archived_email,
                        "password": secrets.token_urlsafe(48),
                        "is_active": False,
                        "is_superuser": False,
                        "is_verified": False,
                        "updated_at": datetime.datetime.now(datetime.timezone.utc),
                    },
                )
