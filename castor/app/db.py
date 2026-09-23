import contextlib
import datetime
from typing import AsyncGenerator
from uuid import UUID

from fastapi import Depends
from fastapi_users.db import SQLAlchemyBaseUserTableUUID, SQLAlchemyUserDatabase
from fastapi_users_db_sqlalchemy.generics import GUID
from sqlalchemy import JSON, DateTime, ForeignKey, func, inspect, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    relationship,
)

from castor.configs import settings

DATABASE_URL = settings.DATABASE_URL


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), insert_default=func.now()
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), insert_default=func.now()
    )


class User(TimestampMixin, SQLAlchemyBaseUserTableUUID, Base):
    token_version: Mapped[int] = mapped_column(default=0, server_default="0", nullable=False)
    # True once we've shown the post-login "Set up a passkey?" offer (or the user
    # dismissed it). We never re-show it after this flips. User can still enrol
    # voluntarily via /security.
    passkey_offer_dismissed: Mapped[bool] = mapped_column(
        default=False, server_default="0", nullable=False
    )
    # Backup email for account recovery. ``recovery_email`` is the address
    # proposed by the user; ``recovery_email_verified`` flips to True only
    # after a successful 6-digit-code round-trip. Both nullable; absence
    # means the user has not opted into account recovery.
    recovery_email: Mapped[str | None] = mapped_column(nullable=True, index=True)
    recovery_email_verified: Mapped[bool] = mapped_column(
        default=False, server_default="0", nullable=False
    )

    habit_list: Mapped["HabitListModel"] = relationship(
        back_populates="user", uselist=False, cascade="all, delete-orphan"
    )
    configs: Mapped["UserConfigsModel"] = relationship(
        back_populates="user", uselist=False, cascade="all, delete-orphan"
    )
    note_images: Mapped["UserNoteImageModel"] = relationship(
        back_populates="user", uselist=True, cascade="all, delete-orphan"
    )
    webauthn_credentials: Mapped[list["WebAuthnCredential"]] = relationship(
        back_populates="user", uselist=True, cascade="all, delete-orphan"
    )


class HabitListModel(TimestampMixin, Base):
    __tablename__ = "habit_list"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    data: Mapped[dict] = mapped_column(JSON, nullable=False)

    user_id = mapped_column(GUID, ForeignKey("user.id"), index=True)
    user = relationship("User", back_populates="habit_list")


class UserIdentityModel(TimestampMixin, Base):
    __tablename__ = "customer"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    email: Mapped[str] = mapped_column(index=True, unique=True)
    customer_id: Mapped[str] = mapped_column(index=True, unique=True)
    provider: Mapped[str] = mapped_column(index=True)
    activated: Mapped[bool] = mapped_column(default=False)
    data: Mapped[dict] = mapped_column(JSON, nullable=False)

    def __str__(self) -> str:
        return f"{self.email}<{self.customer_id}> ({self.provider})"


class UserConfigsModel(TimestampMixin, Base):
    __tablename__ = "user_configs"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    user_id = mapped_column(GUID, ForeignKey("user.id"), index=True)
    user = relationship("User", back_populates="configs")

    # Example config field
    config_data: Mapped[dict] = mapped_column(JSON, nullable=False)


class UserNoteImageModel(TimestampMixin, Base):
    __tablename__ = "user_images"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    unique_id: Mapped[UUID] = mapped_column(GUID, unique=True, index=True)
    user_id = mapped_column(GUID, ForeignKey("user.id"), index=True)
    user = relationship("User", back_populates="note_images")

    blob: Mapped[bytes] = mapped_column("blob", nullable=False)
    extra: Mapped[dict] = mapped_column(JSON, nullable=True)


class UserApiTokenModel(TimestampMixin, Base):
    """Standalone API token table. No ORM relationship to User — query by user_id."""

    __tablename__ = "user_api_tokens"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    token: Mapped[str] = mapped_column(unique=True, index=True)
    user_id = mapped_column(GUID, ForeignKey("user.id"), unique=True, index=True)
    name: Mapped[str | None] = mapped_column(default=None)
    extra: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class WebAuthnCredential(TimestampMixin, Base):
    """WebAuthn / Passkey credential storage."""

    __tablename__ = "webauthn_credential"

    id: Mapped[bytes] = mapped_column(primary_key=True)  # credential_id (raw bytes)
    user_id = mapped_column(GUID, ForeignKey("user.id"), index=True)
    user = relationship("User", back_populates="webauthn_credentials")

    public_key: Mapped[bytes] = mapped_column(nullable=False)
    sign_count: Mapped[int] = mapped_column(default=0, nullable=False)
    transports: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)

    # Resident key / passkey fields
    aaguid: Mapped[bytes | None] = mapped_column(nullable=True)
    backup_eligible: Mapped[bool] = mapped_column(default=False)
    backup_state: Mapped[bool] = mapped_column(default=False)

    # Optional: human-readable name for the credential
    name: Mapped[str | None] = mapped_column(nullable=True)


class RecoveryEmailChallenge(TimestampMixin, Base):
    """Time-limited email-ownership proof for the recovery email flow.

    The user proposes a backup address; we mail a 6-digit code. They
    reply with the code; we flip ``user.recovery_email`` plus its
    ``verified`` flag in a single transaction.

    The code is SHA-256-hashed at rest, expires in 15 minutes, and is
    consumed once. The (user_id, token_version) pair is recorded so a
    parallel sign-out invalidates in-flight challenges.
    """

    __tablename__ = "recovery_email_challenge"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    email: Mapped[str] = mapped_column(index=True)
    code_hash: Mapped[str]
    expires_at: Mapped[datetime.datetime]
    used: Mapped[bool] = mapped_column(default=False, server_default="0", nullable=False)
    user_id = mapped_column(GUID, ForeignKey("user.id"), index=True)
    token_version: Mapped[int | None] = mapped_column(nullable=True)


class PasswordResetCode(TimestampMixin, Base):
    """12-digit reset code with SHA-256 hash, 10-minute TTL, single-use."""

    __tablename__ = "password_reset_code"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    email: Mapped[str] = mapped_column(index=True, nullable=False)
    # Nullable for legacy rows only. Unbound codes are never accepted or backfilled.
    # No FK: account deletion must not be blocked by expiring recovery rows.
    user_id: Mapped[UUID | None] = mapped_column(GUID, nullable=True)
    token_version: Mapped[int | None] = mapped_column(nullable=True)
    code_hash: Mapped[str] = mapped_column(nullable=False)  # SHA-256(code)
    expires_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    used: Mapped[bool] = mapped_column(default=False)


# SSL Mode: https://www.postgresql.org/docs/9.0/libpq-ssl.html#LIBPQ-SSL-SSLMODE-STATEMENTS
# p.s. asyncpg us ssl instead of sslmode: https://github.com/tortoise/aerich/issues/310
connect_args = {}
if settings.DATABASE_URL.startswith("postgresql"):
    connect_args = {"ssl": "allow"}
engine = create_async_engine(
    DATABASE_URL, connect_args=connect_args, pool_pre_ping=True
)
async_session_maker = async_sessionmaker(engine, expire_on_commit=False)


async def create_db_and_tables():
    # Register auxiliary models before create_all, including isolated test apps.
    from castor.app import rate_limits, audit, challenges, circles
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # create_all does not add columns to existing tables. Run before serving.
        columns = await conn.run_sync(lambda sync: {c["name"] for c in inspect(sync).get_columns("user")})
        if "token_version" not in columns:
            await conn.execute(text('ALTER TABLE "user" ADD COLUMN token_version INTEGER NOT NULL DEFAULT 0'))
        if "passkey_offer_dismissed" not in columns:
            await conn.execute(text(
                'ALTER TABLE "user" ADD COLUMN passkey_offer_dismissed BOOLEAN NOT NULL DEFAULT FALSE'
            ))
        if "recovery_email" not in columns:
            await conn.execute(text(
                'ALTER TABLE "user" ADD COLUMN recovery_email VARCHAR NULL'
            ))
            await conn.execute(text(
                'CREATE INDEX IF NOT EXISTS ix_user_recovery_email ON "user" (recovery_email)'
            ))
        if "recovery_email_verified" not in columns:
            await conn.execute(text(
                'ALTER TABLE "user" ADD COLUMN recovery_email_verified BOOLEAN NOT NULL DEFAULT FALSE'
            ))
        # Existing users that have at least one passkey registered
        # should be treated as "dismissed" — they've already onboarded.
        await conn.execute(text(
            'UPDATE "user" SET passkey_offer_dismissed = TRUE '
            'WHERE id IN (SELECT DISTINCT user_id FROM webauthn_credential)'
        ))
        reset_columns = await conn.run_sync(
            lambda sync: {c["name"] for c in inspect(sync).get_columns("password_reset_code")}
        )
        if "user_id" not in reset_columns:
            # Match GUID's native PostgreSQL UUID / portable CHAR(36) storage.
            guid_type = GUID().compile(dialect=conn.dialect)
            await conn.execute(text(f'ALTER TABLE password_reset_code ADD COLUMN user_id {guid_type} NULL'))
        if "token_version" not in reset_columns:
            await conn.execute(text('ALTER TABLE password_reset_code ADD COLUMN token_version INTEGER NULL'))
        # Never infer identity/version from today's account for an old code.
        await conn.execute(text(
            'UPDATE password_reset_code SET used = TRUE '
            'WHERE user_id IS NULL OR token_version IS NULL'
        ))


async def get_async_session() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_maker() as session:
        yield session


get_async_session_context = contextlib.asynccontextmanager(get_async_session)


async def get_user_db(session: AsyncSession = Depends(get_async_session)):
    yield SQLAlchemyUserDatabase(session, User)