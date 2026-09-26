import asyncio
import contextlib
import datetime
from typing import AsyncGenerator
from uuid import UUID

from fastapi import Depends
from fastapi_users.db import SQLAlchemyBaseUserTableUUID, SQLAlchemyUserDatabase
from fastapi_users_db_sqlalchemy.generics import GUID
from sqlalchemy import JSON, DateTime, ForeignKey, event, func, inspect, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    relationship,
)

from castor.configs import settings
from castor.logger import logger

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
    # F4: optimistic-locking version for the single-blob habit list.
    #
    # The whole user's habit set is one JSON row, so a read-modify-write race
    # covers every habit at once and the last writer silently wins. Writers now
    # pass the version they read and the UPDATE is conditional on it, so a
    # concurrent writer is detected and retried instead of clobbering.
    version: Mapped[int] = mapped_column(default=0, server_default="0", nullable=False)

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


class SchemaMigration(Base):
    """Applied schema versions (F23).

    F23: the app previously ran every ``ALTER TABLE`` on every boot, with a
    check-then-ALTER race that could crash-loop the container. Recording the
    applied version lets boot skip DDL entirely once the database is current,
    which is the cheap, correct step toward a real migration tool.

    ``version`` is the primary key so the table doubles as the migration lock:
    the boot transaction writes to it first, which under SQLite takes the
    RESERVED lock and serialises concurrent starts.
    """

    __tablename__ = "schema_migration"

    version: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    applied_at: Mapped[int] = mapped_column(nullable=False, default=0)


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
connect_args: dict = {}
if settings.DATABASE_URL.startswith("postgresql"):
    connect_args = {"ssl": "allow"}

# F9: SQLite was running with journal_mode=delete and foreign_keys=0.
#
# journal_mode=delete takes a whole-file exclusive lock for the duration of
# each write, so a single slow write blocks every reader. With two write paths
# (gunicorn worker + the in-process storage layer) on a 1 vCPU host, concurrent
# writes serialise hard and surface as "database is locked". WAL lets readers
# proceed during a write.
#
# foreign_keys=0 is the more dangerous one: SQLite defaults it OFF per
# connection, so the seven ForeignKey declarations in this module were simply
# never enforced. Nothing cascaded and nothing was rejected.
if DATABASE_URL.startswith("sqlite"):
    connect_args["timeout"] = 30

engine = create_async_engine(
    DATABASE_URL, connect_args=connect_args, pool_pre_ping=True
)

# Both pragmas below are PER CONNECTION and therefore cannot be set once at
# import time — a pooled connection silently reverts them. They must be
# applied on every checkout, which is what the 'connect' event does.
if DATABASE_URL.startswith("sqlite"):
    @event.listens_for(engine.sync_engine, "connect")
    def _set_sqlite_pragmas(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        try:
            # busy_timeout gives SQLite a bounded, escalating retry window
            # instead of failing immediately on a transient lock.
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA busy_timeout=5000")
            # NORMAL is the documented safe pairing with WAL: durable across
            # application crashes, at risk only on OS/power loss, which the
            # WAL + external backup strategy already covers.
            cursor.execute("PRAGMA synchronous=NORMAL")
        finally:
            cursor.close()


async_session_maker = async_sessionmaker(engine, expire_on_commit=False)


# F23: migrations used to run on EVERY boot, and the "check column exists,
# then ALTER" pattern was a confirmed startup race — two instances starting
# together both saw the column missing, both issued ALTER, and the loser died
# with "duplicate column name", failing lifespan startup and crash-looping the
# container under `restart: unless-stopped`.
#
# Three changes make this safe without a full Alembic adoption:
#   1. A `schema_migration` row is written as the FIRST statement of the
#      transaction. On SQLite that takes the RESERVED write lock for the
#      whole block, so a second instance blocks on it rather than racing.
#   2. The block retries with backoff, so a contender waits for the winner
#      instead of dying.
#   3. `SCHEMA_VERSION` gates the whole thing: once the database records the
#      version it is already at, boot does no DDL at all.
SCHEMA_VERSION = 5


async def _acquire_migration_lock(conn) -> None:
    """Take the database write lock by writing first, before any DDL.

    On SQLite a transaction only takes its lock when the first *write*
    statement runs, so issuing the UPDATE as the very first statement is what
    makes the rest of the block mutually exclusive against another process.
    On PostgreSQL the row lock behaves the same way.
    """
    await conn.execute(
        text(
            "INSERT INTO schema_migration (version, applied_at) VALUES (0, 0) "
            "ON CONFLICT (version) DO NOTHING"
        )
    )
    await conn.execute(
        text("UPDATE schema_migration SET applied_at = applied_at WHERE version = 0")
    )


async def _schema_is_current(conn) -> bool:
    """True once the database records a version >= SCHEMA_VERSION.

    Tolerates the table not existing yet (first ever boot): that is simply
    "not current", and the migration block will create it.
    """
    if conn.dialect.name == "sqlite":
        exists = (
            await conn.execute(
                text("SELECT name FROM sqlite_master WHERE type='table' AND name='schema_migration'")
            )
        ).scalar()
    else:
        exists = (
            await conn.execute(
                text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_name = 'schema_migration'"
                )
            )
        ).scalar()
    if not exists:
        return False
    row = (await conn.execute(text("SELECT MAX(version) FROM schema_migration"))).scalar()
    return row is not None and row >= SCHEMA_VERSION


async def _apply_migrations(conn) -> None:
    """Additive, idempotent schema migrations. Each is safe to re-run."""
    await conn.run_sync(Base.metadata.create_all)
    await _acquire_migration_lock(conn)

    # create_all does not add columns to existing tables. Run before serving.
    columns = await conn.run_sync(lambda sync: {c["name"] for c in inspect(sync).get_columns("user")})
    for column, ddl in (
        ("token_version", 'ALTER TABLE "user" ADD COLUMN token_version INTEGER NOT NULL DEFAULT 0'),
        (
            "passkey_offer_dismissed",
            'ALTER TABLE "user" ADD COLUMN passkey_offer_dismissed BOOLEAN NOT NULL DEFAULT FALSE',
        ),
        ("recovery_email", 'ALTER TABLE "user" ADD COLUMN recovery_email VARCHAR NULL'),
        (
            "recovery_email_verified",
            'ALTER TABLE "user" ADD COLUMN recovery_email_verified BOOLEAN NOT NULL DEFAULT FALSE',
        ),
    ):
        if column not in columns:
            await conn.execute(text(ddl))

    await conn.execute(text(
        'CREATE INDEX IF NOT EXISTS ix_user_recovery_email ON "user" (recovery_email)'
    ))

    # F4: habit_list gains a version column for optimistic locking. Existing
    # rows start at 0, which is the correct starting point for the CAS loop.
    habit_list_columns = await conn.run_sync(
        lambda sync: {c["name"] for c in inspect(sync).get_columns("habit_list")}
    )
    if "version" not in habit_list_columns:
        await conn.execute(text(
            "ALTER TABLE habit_list ADD COLUMN version INTEGER NOT NULL DEFAULT 0"
        ))

    # Slice 24d: the unique constraint on circle_habit used to be
    # (circle_id, habit_id) — sufficient when only the owner could
    # share. We extended it to (circle_id, habit_id, owner_user_id)
    # so two members can each share their own copy of a habit with
    # the same string id. Drop the old index if it's a 2-column
    # one, then re-create it as a 3-column unique index.
    #
    # F23: the DROP + CREATE used to run unconditionally on every boot. If
    # circle_habit ever held duplicate rows, CREATE UNIQUE INDEX raised,
    # the transaction rolled back (leaving the old index already dropped)
    # and the app could not start at all.
    #
    # The dedupe DELETE runs FIRST, so duplicate rows can never make the
    # rebuild fail: a latent data problem degrades the constraint rather than
    # bricking startup.
    #
    # Detection must consult get_unique_constraints, NOT get_indexes. SQLite
    # materialises a UNIQUE constraint as `sqlite_autoindex_*`, and SQLAlchemy
    # deliberately omits autoindexes from get_indexes() — so an index-based
    # check always reports "missing" on a create_all-built table and tries to
    # add a second, redundant unique index.
    await conn.execute(text(
        "DELETE FROM circle_habit WHERE id NOT IN ("
        "SELECT MIN(id) FROM circle_habit "
        "GROUP BY circle_id, habit_id, owner_user_id)"
    ))
    want = {"circle_id", "habit_id", "owner_user_id"}
    has_constraint = await conn.run_sync(
        lambda sync: any(
            set(uq["column_names"]) == want
            for uq in inspect(sync).get_unique_constraints("circle_habit")
        )
    )
    # Older databases may instead carry an explicitly-named unique index from
    # the previous boot migration; treat that as satisfying the requirement.
    if not has_constraint:
        has_constraint = await conn.run_sync(
            lambda sync: any(
                ix.get("unique") and set(ix["column_names"]) == want
                for ix in inspect(sync).get_indexes("circle_habit")
            )
        )
    if not has_constraint:
        await conn.execute(text(
            "CREATE UNIQUE INDEX circle_habit_unique "
            "ON circle_habit (circle_id, habit_id, owner_user_id)"
        ))

    # Existing users that have at least one passkey registered
    # should be treated as "dismissed" — they've already onboarded.
    await conn.execute(text(
        'UPDATE "user" SET passkey_offer_dismissed = TRUE '
        "WHERE id IN (SELECT DISTINCT user_id FROM webauthn_credential)"
    ))
    reset_columns = await conn.run_sync(
        lambda sync: {c["name"] for c in inspect(sync).get_columns("password_reset_code")}
    )
    if "user_id" not in reset_columns:
        # Match GUID's native PostgreSQL UUID / portable CHAR(36) storage.
        guid_type = GUID().compile(dialect=conn.dialect)
        await conn.execute(text(f"ALTER TABLE password_reset_code ADD COLUMN user_id {guid_type} NULL"))
    if "token_version" not in reset_columns:
        await conn.execute(text("ALTER TABLE password_reset_code ADD COLUMN token_version INTEGER NULL"))
    # Never infer identity/version from today's account for an old code.
    await conn.execute(text(
        "UPDATE password_reset_code SET used = TRUE "
        "WHERE user_id IS NULL OR token_version IS NULL"
    ))

    await conn.execute(
        text(
            "INSERT INTO schema_migration (version, applied_at) "
            "VALUES (:v, :t) ON CONFLICT (version) DO NOTHING"
        ),
        {"v": SCHEMA_VERSION, "t": int(datetime.datetime.now(datetime.timezone.utc).timestamp())},
    )


async def create_db_and_tables():
    """Create/upgrade the schema once, under a lock, before serving.

    Retries on lock contention so two instances starting together both reach
    a ready state instead of one crash-looping.
    """
    from castor.app import rate_limits, audit, challenges, circles  # noqa: F401

    delay = 0.5
    last_error: Exception | None = None
    for attempt in range(6):
        try:
            async with engine.begin() as conn:
                if await _schema_is_current(conn):
                    return
                await _apply_migrations(conn)
            return
        except OperationalError as exc:
            # Another instance holds the migration lock, or SQLite is briefly
            # busy. Wait and retry rather than dying during startup.
            last_error = exc
            logger.warning(
                f"Schema migration attempt {attempt + 1} contended ({exc.__class__.__name__}); "
                f"retrying in {delay:.1f}s"
            )
            await asyncio.sleep(delay)
            delay = min(delay * 2, 8)
    raise RuntimeError(
        f"Could not acquire the schema migration lock after repeated attempts: {last_error}"
    )


async def get_async_session() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_maker() as session:
        yield session


get_async_session_context = contextlib.asynccontextmanager(get_async_session)


async def get_user_db(session: AsyncSession = Depends(get_async_session)):
    yield SQLAlchemyUserDatabase(session, User)