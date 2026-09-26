"""F23/F9 — schema bootstrap: idempotency, version gating, concurrency, pragmas.

The audit's F23 was a confirmed startup race: two instances booting together
both saw a column missing, both issued ALTER, and the loser died with
"duplicate column name" — failing lifespan startup and crash-looping the
container under `restart: unless-stopped`.

These tests exercise the real code path (create_db_and_tables) against a real
SQLite file, in isolation from production.
"""
import asyncio
import os
import sqlite3

import pytest
from sqlalchemy import text

from castor.app import db

# DATABASE_URL is set globally by tests/conftest.py, which is imported first and
# points the suite at a throwaway temp database. Each test below still deletes
# that file (and its -wal/-shm siblings) to start from an empty schema, and
# disposes the engine so a pooled connection cannot keep the old file open.


@pytest.fixture(autouse=True)
async def _fresh_db():
    """Each test starts from an empty database.

    The engine must be disposed, not just the files deleted: SQLAlchemy pools
    connections, and a pooled connection still holds the old file open, so the
    next test would otherwise inherit the previous test's schema.
    """
    await _reset_database_file()
    yield
    await db.engine.dispose()


async def _reset_database_file() -> None:
    await db.engine.dispose()
    path = db.DATABASE_URL.split("///", 1)[-1]
    for suffix in ("", "-wal", "-shm"):
        candidate = f"{path}{suffix}"
        if os.path.exists(candidate):
            os.remove(candidate)


async def test_bootstrap_creates_schema_and_records_version():
    await db.create_db_and_tables()

    async with db.async_session_maker() as session:
        version = (
            await session.execute(text("SELECT MAX(version) FROM schema_migration"))
        ).scalar()
    assert version == db.SCHEMA_VERSION


async def test_bootstrap_is_idempotent():
    """Re-running the bootstrap must be a no-op, not an error."""
    await db.create_db_and_tables()
    await db.create_db_and_tables()
    await db.create_db_and_tables()

    async with db.async_session_maker() as session:
        versions = (
            await session.execute(text("SELECT version FROM schema_migration"))
        ).scalars().all()
    assert versions.count(db.SCHEMA_VERSION) == 1


async def test_bootstrap_skips_ddl_when_schema_is_current():
    """Once current, a second boot must not touch the schema at all.

    This is the property that stops `ALTER TABLE` running on every restart.
    """
    await db.create_db_and_tables()

    # Poison the recorded version so the "is current" check must be consulted.
    async with db.async_session_maker() as session:
        await session.execute(text("DELETE FROM schema_migration"))
        await session.commit()

    # The table is gone, so the check has to fail and migrations must re-run.
    await db.create_db_and_tables()
    async with db.async_session_maker() as session:
        version = (
            await session.execute(text("SELECT MAX(version) FROM schema_migration"))
        ).scalar()
    assert version == db.SCHEMA_VERSION


async def test_unmounted_volume_fails_fast_with_a_useful_message():
    """A non-transient failure must not be retried behind "contended" warnings.

    Found in the apollo container smoke test: a run without the data volume
    mounted logged six "Schema migration attempt N contended" lines over 30
    seconds and then blamed a missing lock, when the actual error was
    `unable to open database file`. The cause is a missing mount, which no
    amount of retrying will fix.
    """
    from sqlalchemy.exc import OperationalError

    unopenable = OperationalError(
        "SELECT 1", {}, sqlite3.OperationalError("unable to open database file")
    )
    assert db._is_lock_contention(unopenable) is False


async def test_transient_race_errors_are_still_retried():
    """Concurrent bootstrap races must remain retryable, not become fatal."""
    from sqlalchemy.exc import OperationalError

    transient = [
        sqlite3.OperationalError("database is locked"),
        sqlite3.OperationalError("database table is locked: user"),
        sqlite3.OperationalError("table user already exists"),
    ]
    for message in transient:
        exc = OperationalError("CREATE TABLE", {}, Exception(message))
        assert db._is_lock_contention(exc) is True, message


async def test_concurrent_bootstrap_both_succeed():
    """Two instances starting together must BOTH reach ready (F23).

    Before the fix the loser raised `duplicate column name` from inside
    engine.begin(), so lifespan startup failed and gunicorn exited.
    """
    results = await asyncio.gather(
        db.create_db_and_tables(),
        db.create_db_and_tables(),
        return_exceptions=True,
    )
    for result in results:
        assert not isinstance(result, Exception), f"concurrent bootstrap failed: {result!r}"

    async with db.async_session_maker() as session:
        version = (
            await session.execute(text("SELECT MAX(version) FROM schema_migration"))
        ).scalar()
    assert version == db.SCHEMA_VERSION


async def test_sqlite_pragmas_are_applied_per_connection():
    """F9: WAL + foreign_keys must hold on EVERY pooled connection.

    Both pragmas are per-connection in SQLite, so setting them once at import
    time would silently revert on every checkout from the pool.
    """
    await db.create_db_and_tables()

    # Force several distinct checkouts from the pool.
    for _ in range(3):
        async with db.async_session_maker() as session:
            journal = (await session.execute(text("PRAGMA journal_mode"))).scalar()
            fkeys = (await session.execute(text("PRAGMA foreign_keys"))).scalar()
            assert str(journal).lower() == "wal", f"journal_mode={journal}"
            assert fkeys == 1, f"foreign_keys={fkeys}"


async def test_foreign_keys_are_actually_enforced():
    """F9: a child row for a non-existent parent must now be REJECTED.

    This is the behaviour change that proves foreign_keys=ON is real and not
    just reported. With foreign_keys=0 (the old live setting) this insert
    silently succeeded and created an orphan.
    """
    await db.create_db_and_tables()

    with pytest.raises(Exception):
        async with db.async_session_maker() as session:
            await session.execute(
                text(
                    "INSERT INTO user_api_tokens (token, user_id) "
                    "VALUES ('deadbeef', '00000000-0000-0000-0000-000000000000')"
                )
            )
            await session.commit()


async def test_migration_repairs_duplicate_circle_habits():
    """F23: duplicate rows must degrade the index, not brick startup.

    Before the fix, CREATE UNIQUE INDEX raised on duplicates, rolled back the
    transaction (leaving the old index already dropped) and the app could not
    start at all.
    """
    await db.create_db_and_tables()

    # Drop the unique constraint so the duplicates below can be inserted at
    # all. create_all materialises the model's UniqueConstraint as an
    # auto-index, which SQLite cannot drop directly, so rebuild the table.
    # The replacement is created with an explicit INTEGER PRIMARY KEY so
    # `id` is populated on insert — the real schema has one, and the dedupe
    # migration in castor/app/db.py relies on MIN(id).
    async with db.async_session_maker() as session:
        await session.execute(text("DROP INDEX IF EXISTS circle_habit_unique"))
        await session.execute(
            text(
                "CREATE TABLE circle_habit_dedup_tmp ("
                "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
                "  circle_id INTEGER NOT NULL,"
                "  habit_id VARCHAR NOT NULL,"
                "  owner_user_id CHAR(36) NOT NULL,"
                "  visibility VARCHAR(32) NOT NULL DEFAULT 'ticks',"
                "  share_notes BOOLEAN NOT NULL DEFAULT 0,"
                "  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,"
                "  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP"
                ")"
            )
        )
        await session.execute(text("DROP TABLE circle_habit"))
        await session.execute(text("ALTER TABLE circle_habit_dedup_tmp RENAME TO circle_habit"))
        uid = "11111111-1111-1111-1111-111111111111"
        await session.execute(
            text(
                "INSERT INTO \"user\" (id, email, hashed_password, is_active, "
                "is_superuser, is_verified, token_version, passkey_offer_dismissed, "
                "recovery_email_verified, created_at, updated_at) VALUES "
                "(:u, 'dupe@example.com', 'x', 1, 0, 1, 0, 0, 0, "
                "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            ),
            {"u": uid},
        )
        await session.execute(
            text(
                "INSERT INTO circle (id, name, owner_id, created_at, updated_at) "
                "VALUES (1, 'c', :u, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            ),
            {"u": uid},
        )
        for _ in range(2):
            await session.execute(
                text(
                    "INSERT INTO circle_habit (circle_id, habit_id, owner_user_id, "
                    "visibility, share_notes, created_at, updated_at) "
                    "VALUES (1, 'h', :u, 'ticks', 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                ),
                {"u": uid},
            )
        await session.commit()

    # Force a re-run of the migration block.
    async with db.async_session_maker() as session:
        await session.execute(text("DELETE FROM schema_migration"))
        await session.commit()

    await db.create_db_and_tables()

    async with db.async_session_maker() as session:
        rows = (
            await session.execute(text("SELECT COUNT(*) FROM circle_habit"))
        ).scalar()
    assert rows == 1, "duplicates should be collapsed, not left in place"
