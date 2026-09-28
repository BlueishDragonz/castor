"""Tests for the F4 durability redesign: optimistic locking, merge-on-conflict,
bounded cache, and durability acknowledgement.

These cover the storage layer's contract directly rather than through the HTTP
surface, because the interesting cases are concurrent-writer races that are
awkward to provoke through a request cycle and easy to provoke deterministically
at the storage layer.

  - a stale version raises HabitListConflict rather than clobbering
  - the conflict path MERGES and retries, and does not lose either side's days
  - the merge path works at all (regression: it once read a non-existent
    `.data` attribute and raised AttributeError at the moment it was needed)
  - the cache is bounded and evicts least-recently-used
  - flush() is a no-op when clean, and is what makes an ack mean "on disk"
  - shutdown flush_all() persists pending writes
"""
import asyncio
import os
import tempfile

_TMP = tempfile.TemporaryDirectory()
os.environ['DATABASE_URL'] = f'sqlite+aiosqlite:///{_TMP.name}/test.db'
os.environ['JWT_SECRET'] = 'test-only-jwt-secret'
os.environ['RESET_PASSWORD_TOKEN_SECRET'] = 'test-only-reset-secret'
os.environ['NICEGUI_STORAGE_SECRET'] = 'test-only-storage-secret'
os.environ['REQUIRE_ADMIN_FOR_REGISTRATION'] = 'false'

import pytest
from sqlalchemy import text
from unittest.mock import patch

from castor.app import crud
from castor.app.db import User, create_db_and_tables, get_async_session_context
from castor.storage import get_user_dict_storage
from castor.storage.user_db import (
    CACHE_MAX_ENTRIES,
    MAX_WRITE_RETRIES,
    DatabasePersistentDict,
    OptimisticLockError,
)


async def _user(uid: str = "f4-user-1", email: str = "f4@example.com") -> User:
    """Create and persist a real User row.

    Real rows rather than stubs: these tests exercise the actual SQLAlchemy
    mapping and the actual FK to `user`, so a schema mistake surfaces here
    instead of passing against a fake.
    """
    import uuid

    async with get_async_session_context() as session:
        user = User(
            id=uuid.UUID(uid) if _is_uuid(uid) else uuid.uuid4(),
            email=email,
            hashed_password="not-a-real-hash",
            is_active=True,
            is_superuser=False,
            is_verified=True,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


_UUID_CHARS = set("0123456789abcdef-")


def _is_uuid(value: str) -> bool:
    return len(value) == 36 and set(value.lower()) <= _UUID_CHARS


def _habit(habit_id: str, days: dict[str, bool], name: str = "Habit") -> dict:
    return {
        "id": habit_id,
        "name": name,
        "records": [
            {"day": day, "done": done, "timestamp": 0, "text": None}
            for day, done in days.items()
        ],
    }


async def _make_user_and_list(user: User, data: dict) -> None:
    await crud.update_user_habit_list(user, data)


@pytest.fixture(autouse=True)
async def _schema():
    await create_db_and_tables()


# ---------------------------------------------------------------------------
# Optimistic locking
# ---------------------------------------------------------------------------


async def test_stale_version_raises_conflict_instead_of_clobbering():
    """A writer holding a stale version must be REJECTED, not silently win.

    This is the F4 defect: previously the write was SELECT -> mutate -> COMMIT
    with no version check, so the second writer's commit discarded the first's
    changes with no error anywhere.
    """
    user = await _user("f4-a", "f4a@example.com")
    await _make_user_and_list(user, {"habits": []})

    v0 = await crud.get_user_habit_list(user)
    assert v0 is not None
    assert v0.version == 0

    # Writer A commits from version 0.
    v1 = await crud.update_user_habit_list(
        user, {"habits": [_habit("h1", {"2026-01-01": True})]}, expected_version=0
    )
    assert v1 == 1

    # Writer B still believes it is at version 0. It must be told.
    with pytest.raises(crud.HabitListConflict):
        await crud.update_user_habit_list(
            user,
            {"habits": [_habit("h2", {"2026-01-02": True})]},
            expected_version=0,
        )

    # A's write survived; B's did not overwrite it.
    stored = await crud.get_user_habit_list(user)
    assert stored.version == 1
    assert [h["id"] for h in stored.data["habits"]] == ["h1"]


async def test_conflict_retry_merges_and_preserves_both_sides():
    """The storage layer's conflict path must merge, not discard.

    Two devices tick different days of the same user's habits. The loser's
    changes are re-applied on top of the winner's committed state, so a
    concurrent tick is never lost — which is the user-visible harm F4
    described.
    """
    user = await _user("f4-b", "f4b@example.com")
    await _make_user_and_list(user, {"habits": []})
    await crud.update_user_habit_list(
        user,
        {"habits": [_habit("h1", {"2026-01-01": False})]},
        expected_version=0,
    )

    storage = get_user_dict_storage()
    habit_list = await storage.get_user_habit_list(user)
    persistent = storage._cache[user.id]

    # Local (device A) change: tick 2026-01-01.
    habit = await habit_list.get_habit_by("h1")
    assert habit is not None
    await habit.tick(_day("2026-01-01"), True, None)
    assert persistent._dirty

    # Meanwhile another writer commits 2026-01-02 behind our back, moving the
    # version out from under us.
    current = await crud.get_user_habit_list(user)
    await crud.update_user_habit_list(
        user,
        {"habits": [_habit("h1", {"2026-01-01": False, "2026-01-02": True})]},
        expected_version=current.version,
    )

    # Our flush must now hit a conflict, re-read, merge, and succeed.
    await persistent.flush()

    stored = await crud.get_user_habit_list(user)
    by_id = {h["id"]: h for h in stored.data["habits"]}
    days_1 = {r["day"]: r["done"] for r in by_id["h1"]["records"]}
    # Neither side's tick was lost.
    assert days_1.get("2026-01-01") is True, "our local tick was lost"
    assert days_1.get("2026-01-02") is True, "the other writer's tick was lost"
    assert not persistent._dirty, "state should be clean after a successful flush"


def _day(value: str):
    import datetime

    return datetime.date.fromisoformat(value)


# ---------------------------------------------------------------------------
# Cache bound (F5)
# ---------------------------------------------------------------------------


async def test_cache_is_bounded_and_evicts_least_recently_used():
    """The cache must not grow without bound (F5).

    Every user who ever authenticated used to leave a full copy of their habit
    list resident forever, which is what made an OOM kill (F3) destructive.
    """
    storage = get_user_dict_storage()
    storage._cache.clear()

    # Fill past the cap.
    for i in range(CACHE_MAX_ENTRIES + 5):
        user = await _user(f"f5-{i}", f"f5-{i}@example.com")
        await _make_user_and_list(user, {"habits": []})
        await storage.get_user_habit_list(user)

    assert len(storage._cache) <= CACHE_MAX_ENTRIES, (
        f"cache grew to {len(storage._cache)}, above the {CACHE_MAX_ENTRIES} cap"
    )


async def test_touching_a_user_refreshes_its_cache_position():
    """Eviction is LRU, not insertion-order, so an active user is not dropped.

    Sized so the cache overflows by exactly two entries: the two
    least-recently-used go, the recently-touched one stays. If eviction were
    plain insertion order, the touched entry would be the first casualty.
    """
    storage = get_user_dict_storage()
    storage._cache.clear()

    users = []
    for i in range(3):
        user = await _user(f"f5lru-{i}", f"f5lru-{i}@example.com")
        await _make_user_and_list(user, {"habits": []})
        await storage.get_user_habit_list(user)
        users.append(user)

    # Touch the oldest so it becomes most-recently-used. Order is now
    # [users[1], users[2], users[0]].
    await storage.get_user_habit_list(users[0])

    # Push the cache exactly two over the cap.
    for i in range(CACHE_MAX_ENTRIES - 1):
        extra = await _user(f"f5fill-{i}", f"f5fill-{i}@example.com")
        await _make_user_and_list(extra, {"habits": []})
        await storage.get_user_habit_list(extra)

    assert users[0].id in storage._cache, "a recently used entry was evicted"
    assert users[1].id not in storage._cache, "the true LRU entry should be gone"


# ---------------------------------------------------------------------------
# Durability acknowledgement (F2)
# ---------------------------------------------------------------------------


async def test_flush_is_a_noop_when_clean():
    """A clean flush must not touch the database.

    This is what lets the durability middleware run on every non-GET request
    without costing anything on the common path.
    """
    user = await _user("f2-a", "f2a@example.com")
    await _make_user_and_list(user, {"habits": []})

    storage = get_user_dict_storage()
    habit_list = await storage.get_user_habit_list(user)
    persistent = storage._cache[user.id]

    await persistent.flush()
    version_before = (await crud.get_user_habit_list(user)).version

    await persistent.flush()
    await persistent.flush()

    assert (await crud.get_user_habit_list(user)).version == version_before, (
        "flushing a clean entry must not bump the version"
    )


async def test_flush_persists_pending_changes():
    """flush() is what makes an acknowledged write durable (F2)."""
    user = await _user("f2-b", "f2b@example.com")
    await _make_user_and_list(user, {"habits": [_habit("h1", {"2026-01-01": False})]})

    storage = get_user_dict_storage()
    habit_list = await storage.get_user_habit_list(user)
    persistent = storage._cache[user.id]

    habit = await habit_list.get_habit_by("h1")
    assert habit is not None
    await habit.tick(_day("2026-01-01"), True, None)
    assert persistent._dirty

    await persistent.flush()
    assert not persistent._dirty

    # Read back through a fresh query, bypassing the in-memory copy.
    stored = await crud.get_user_habit_list(user)
    records = {r["day"]: r["done"] for r in stored.data["habits"][0]["records"]}
    assert records["2026-01-01"] is True, "flush did not persist the tick"


async def test_flush_all_persists_every_dirty_entry():
    """Shutdown flush_all() must leave nothing pending."""
    storage = get_user_dict_storage()
    storage._cache.clear()

    users = []
    for i in range(3):
        user = await _user(f"f2all-{i}", f"f2all-{i}@example.com")
        await _make_user_and_list(user, {"habits": [_habit("h1", {"2026-01-01": False})]})
        habit_list = await storage.get_user_habit_list(user)
        habit = await habit_list.get_habit_by("h1")
        assert habit is not None
        await habit.tick(_day("2026-01-01"), True, None)
        users.append(user)

    await storage.flush_all()

    for user in users:
        stored = await crud.get_user_habit_list(user)
        records = {r["day"]: r["done"] for r in stored.data["habits"][0]["records"]}
        assert records["2026-01-01"] is True, (
            f"shutdown flush lost a write for {user.id}"
        )


async def test_flush_all_clears_the_cache():
    """After a shutdown flush there is nothing left to hold in memory."""
    storage = get_user_dict_storage()
    user = await _user("f2clr", "f2clr@example.com")
    await _make_user_and_list(user, {"habits": []})
    await storage.get_user_habit_list(user)
    assert storage._cache

    await storage.flush_all()
    assert storage._cache == {}


# ---------------------------------------------------------------------------
# The merge path must actually run
# ---------------------------------------------------------------------------


async def test_conflict_merge_path_does_not_raise_attributeerror():
    """Regression: the merge path once read a non-existent `.data` attribute.

    `DatabasePersistentDict` is an ObservableDict, which *is* a dict and has no
    `.data` attribute — that belongs to the HabitListModel. The old code read
    `self.data`, so the merge raised AttributeError at precisely the moment a
    concurrent write made the merge necessary. This test drives that path
    deliberately rather than waiting for a real race.
    """
    user = await _user("f4-merge", "f4merge@example.com")
    await _make_user_and_list(user, {"habits": [_habit("h1", {"2026-01-01": False})]})

    storage = get_user_dict_storage()
    habit_list = await storage.get_user_habit_list(user)
    persistent = storage._cache[user.id]

    # Sanity: the in-memory object has no `.data`, which is why the old code
    # was wrong.
    assert not hasattr(persistent, "data")

    habit = await habit_list.get_habit_by("h1")
    assert habit is not None
    await habit.tick(_day("2026-01-01"), True, None)

    # Force a conflict by advancing the stored version behind our back.
    current = await crud.get_user_habit_list(user)
    await crud.update_user_habit_list(
        user,
        {"habits": [_habit("h1", {"2026-01-01": False, "2026-01-03": True})]},
        expected_version=current.version,
    )

    # This is the call that used to raise AttributeError.
    await persistent.flush()

    stored = await crud.get_user_habit_list(user)
    records = {r["day"]: r["done"] for r in stored.data["habits"][0]["records"]}
    assert records["2026-01-01"] is True
    assert records["2026-01-03"] is True


async def test_optimistic_lock_error_is_raised_when_conflicts_persist():
    """Bounded retries must surface a real error, not spin or silently drop.

    A writer that loses the race `MAX_WRITE_RETRIES` times must be told, rather
    than looping forever or giving up quietly — the latter is the F4 defect in
    a new form.
    """
    user = await _user("f4-retry", "f4retry@example.com")
    await _make_user_and_list(user, {"habits": []})

    persistent = DatabasePersistentDict(user, {"habits": []}, version=0)
    # Write directly rather than via the dict, so no debounce task is
    # scheduled. Otherwise flush() runs twice (once directly, once awaiting the
    # in-flight task) and the attempt count doubles for reasons that have
    # nothing to do with the retry bound.
    persistent["habits"] = [_habit("h1", {"2026-01-01": True})]
    persistent._dirty = True
    persistent._pending_backup = True
    persistent._flush_task = None

    # Force EVERY attempt to conflict, including after a re-read.
    #
    # The natural "a competing writer bumps the version each time" simulation
    # does not work: the merge re-reads the version and then writes, so it
    # wins on the second attempt and MAX_WRITE_RETRIES is never reached. That
    # is correct behaviour, not a bug — a single conflict is resolved.
    #
    # To exercise the bound, the write itself is made to always conflict. This
    # is the adversarial case the bound exists for: without it, this is a
    # livelock that looks exactly like progress.
    calls = {"n": 0}

    async def always_conflicts(u, data, expected_version=None):
        calls["n"] += 1
        raise crud.HabitListConflict("simulated permanent conflict")

    with patch.object(crud, "update_user_habit_list", always_conflicts):
        with pytest.raises(OptimisticLockError):
            await persistent.flush()

    # Bounded, not spinning.
    assert calls["n"] == MAX_WRITE_RETRIES, (
        f"expected exactly {MAX_WRITE_RETRIES} attempts, made {calls['n']}"
    )
    # And the write is still marked dirty, so it is retryable rather than lost.
    assert persistent._dirty, "a failed flush must not clear _dirty"
