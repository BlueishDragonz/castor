import uuid
from typing import Optional
import base64

from fastapi import Depends, HTTPException, Request
from fastapi_users import BaseUserManager, FastAPIUsers, UUIDIDMixin, exceptions
from fastapi_users.jwt import decode_jwt, generate_jwt
import jwt
from sqlalchemy import select
from starlette.status import HTTP_429_TOO_MANY_REQUESTS

from fastapi_users.authentication import (
    AuthenticationBackend,
    BearerTransport,
    JWTStrategy,
    Strategy,
)
from fastapi_users.db import SQLAlchemyUserDatabase

from castor.configs import settings
from castor.logger import logger

from . import crud
from .db import User, WebAuthnCredential, get_user_db
from .audit import record

JWT_SECRET = settings.JWT_SECRET
JWT_LIFETIME_SECONDS = settings.JWT_LIFETIME_SECONDS


class UserManager(UUIDIDMixin, BaseUserManager[User, uuid.UUID]):
    reset_password_token_secret = JWT_SECRET
    verification_token_secret = JWT_SECRET

    async def validate_password(self, password: str, user) -> None:
        if len(password) < 12:
            raise exceptions.InvalidPasswordException("Password must be at least 12 characters")

    async def _update(self, user: User, update_dict: dict) -> User:
        changes = dict(update_dict)
        # Never accept an externally supplied version. Increment in the same SQL
        # UPDATE as the password hash, not from a possibly stale Python object.
        changes.pop("token_version", None)
        if changes.get("password") is not None:
            changes["token_version"] = User.token_version + 1
        updated = await super()._update(user, changes)
        if changes.get("password") is not None:
            await record("password_change", user_id=updated.id)
        if changes.get("is_active") is False:
            await record("account_delete", user_id=updated.id)
        return updated

    async def authenticate(self, credentials):
        user = await super().authenticate(credentials)
        await record("login", "success" if user and user.is_active else "failure", user.id if user else None)
        return user

    async def on_after_register(self, user: User, request: Optional[Request] = None):
        await record("register", user_id=user.id)

    async def create(
        self,
        user_create,
        safe: bool = False,
        request: Optional[Request] = None,
    ) -> User:
        """F17: enforce MAX_USER_COUNT, without a check-then-act race.

        This setting was declared in `configs.py` and never read anywhere, so
        an operator who set it to cap registrations would see no effect at all
        — a configuration knob that silently does nothing is worse than no
        knob, because it is trusted.

        The check is in `create` (not `on_after_register`) so the cap is
        enforced BEFORE a row is written; enforcing it afterwards would leave
        an over-cap account that must then be deleted by hand.

        -1 is the documented "unlimited" default and skips the database work
        entirely, so the default deployment pays nothing for this.

        The claim is a single conditional UPDATE (see
        `crud.try_claim_registration_seat`) rather than a count followed by an
        insert. The earlier version did `SELECT COUNT(*)` and then inserted,
        which two simultaneous registrations could both pass — exactly the
        first burst of traffic a cap exists to regulate. The conditional
        UPDATE cannot be passed by two callers at once.

        A claimed seat is released if the user creation then fails, so a
        transient error cannot permanently consume capacity.
        """
        claimed = await crud.try_claim_registration_seat(settings.MAX_USER_COUNT)
        if not claimed:
            # Deliberately NOT `exceptions.UserAlreadyExists`: that maps to a
            # 400 "email already registered", which would tell a prospective
            # user their address is taken. The truth is that the instance is
            # full. A 429 with Retry-After is the accurate signal, and it stops
            # a signup bot from hammering a closed door.
            raise HTTPException(
                status_code=HTTP_429_TOO_MANY_REQUESTS,
                detail=(
                    "This instance is at capacity and is not accepting "
                    "new registrations."
                ),
                headers={"Retry-After": "3600"},
            )
        try:
            return await super().create(user_create, safe=safe, request=request)
        except Exception:
            # The seat was granted but the user was not created. Give it back,
            # or a run of failed signups permanently shrinks the instance.
            await crud.release_registration_seat()
            raise

    async def on_after_forgot_password(
        self, user: User, token: str, request: Optional[Request] = None
    ):
        await record("password_reset_request", user_id=user.id)

    async def on_after_request_verify(
        self, user: User, token: str, request: Optional[Request] = None
    ):
        logger.info("Verification requested for account {}", user.id)

    async def add_webauthn_credential(
        self,
        user: User,
        credential_id: bytes,
        public_key: bytes,
        sign_count: int,
        transports: list[str],
        aaguid: bytes | None = None,
        backup_eligible: bool = False,
        backup_state: bool = False,
        name: str | None = None,
    ) -> WebAuthnCredential:
        from sqlalchemy.ext.asyncio import AsyncSession

        session = self.user_db.session

        cred = WebAuthnCredential(
            id=credential_id,
            user_id=user.id,
            public_key=public_key,
            sign_count=sign_count,
            transports=transports,
            aaguid=aaguid,
            backup_eligible=backup_eligible,
            backup_state=backup_state,
            name=name,
        )
        session.add(cred)
        await session.commit()
        await session.refresh(cred)
        await record("passkey_register", user_id=user.id)
        return cred

    async def get_webauthn_credentials(self, user: User) -> list[WebAuthnCredential]:
        from sqlalchemy import select

        session = self.user_db.session
        result = await session.execute(
            select(WebAuthnCredential).where(WebAuthnCredential.user_id == user.id)
        )
        return list(result.scalars().all())

    async def delete_webauthn_credential(self, user: User, credential_id: bytes) -> bool:
        from sqlalchemy import delete

        session = self.user_db.session
        result = await session.execute(
            delete(WebAuthnCredential).where(
                WebAuthnCredential.id == credential_id,
                WebAuthnCredential.user_id == user.id
            )
        )
        await session.commit()
        if result.rowcount > 0:
            await record("passkey_delete", user_id=user.id)
        return result.rowcount > 0


async def get_user_manager(user_db: SQLAlchemyUserDatabase = Depends(get_user_db)):
    yield UserManager(user_db)


bearer_transport = BearerTransport(tokenUrl="auth/login")
class VersionedJWTStrategy(JWTStrategy):
    async def write_token(self, user: User) -> str:
        return generate_jwt(
            {"sub": str(user.id), "aud": self.token_audience, "ver": user.token_version},
            self.encode_key, self.lifetime_seconds, algorithm=self.algorithm,
        )

    async def read_token(self, token, user_manager):
        if not token:
            return None
        try:
            data = decode_jwt(token, self.decode_key, self.token_audience, algorithms=[self.algorithm])
            version = data.get("ver")
            if type(version) is not int or version < 0:
                return None
            user_id = user_manager.parse_id(data.get("sub"))
        except (jwt.PyJWTError, exceptions.InvalidID, ValueError, TypeError):
            return None
        # A fresh SELECT avoids trusting an identity-map-cached token version.
        user = (await user_manager.user_db.session.execute(
            select(User).where(User.id == user_id).execution_options(populate_existing=True)
        )).scalar_one_or_none()
        return user if user and user.is_active and user.token_version == version else None


jwt_strategy = VersionedJWTStrategy(secret=JWT_SECRET, lifetime_seconds=JWT_LIFETIME_SECONDS)


def get_jwt_strategy() -> JWTStrategy:
    return jwt_strategy


def get_cookie_settings() -> dict:
    """Cookie settings for auth - Strict + Secure in production.

    The Secure decision comes from `settings.is_https_public()`, which reads
    PUBLIC_URL first and falls back to the legacy APP_URL / TLS_TERMINATED
    pair. It previously consulted only APP_URL and TLS_TERMINATED, so an
    operator who configured the documented public origin (FRONTEND_URL /
    PUBLIC_URL) as https but left the SaaS-era APP_URL empty shipped a
    30-day session cookie without Secure. Behind a TLS-terminating reverse
    proxy that is exactly the cookie most worth protecting.
    """
    return {
        "httponly": True,
        "samesite": "strict",
        "secure": settings.is_https_public(),
    }


auth_backend = AuthenticationBackend(
    name="jwt",
    transport=bearer_transport,
    get_strategy=get_jwt_strategy,
)

fastapi_users = FastAPIUsers[User, uuid.UUID](
    get_user_manager, [auth_backend]
)