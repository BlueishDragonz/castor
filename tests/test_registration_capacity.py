"""F17 — the registration cap must be exact, not advisory.

The first implementation counted users and then inserted:

    if await crud.get_user_count() >= MAX_USER_COUNT: refuse
    return await super().create(...)

That is a check-then-act race, and the burst of simultaneous signups it fails
to regulate is exactly the scenario a capacity limit exists for. The
docstring at the time admitted this and called the consequence "one extra
account, not corruption" — true, but avoidable, and the knob is trusted.

The fix claims a seat with a single conditional UPDATE, so the check and the
increment are the same statement and two callers cannot both pass.

These tests assert the property, not the implementation: whatever the code
does, a cap of N must never admit more than N.
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import delete, select

from castor.app import crud
from castor.app import db as db_module
from castor.app.db import RegistrationCapacity, async_session_maker


@pytest.fixture(autouse=True)
async def _clean_capacity():
    """Each test starts from a known counter state, with the table present.

    The session-wide database is created lazily, so a module whose first
    action touches a table may run before anything has called
    create_db_and_tables. Creating the schema here is idempotent and makes the
    module independent of test ordering — the alternative was a test that
    passes in a full-suite run and errors when run alone, or vice versa.
    """
    await db_module.create_db_and_tables()
    async with async_session_maker() as session:
        await session.execute(delete(RegistrationCapacity))
        await session.commit()
    yield


async def test_seat_is_granted_below_the_limit():
    assert await crud.try_claim_registration_seat(limit=3) is True
    assert await crud.try_claim_registration_seat(limit=3) is True
    assert await crud.try_claim_registration_seat(limit=3) is True


async def test_seat_is_refused_at_the_limit():
    for _ in range(2):
        assert await crud.try_claim_registration_seat(limit=2) is True
    assert await crud.try_claim_registration_seat(limit=2) is False


async def test_unlimited_never_consults_the_database():
    """-1 is the documented 'unlimited' default and must stay free."""
    for _ in range(50):
        assert await crud.try_claim_registration_seat(limit=-1) is True


async def test_concurrent_claims_cannot_exceed_the_limit():
    """The regression: N simultaneous claimants against a limit of N.

    With a count-then-insert implementation, several of these observe the same
    pre-count and all succeed. With a conditional UPDATE, the SQLite write lock
    serialises them and only `limit` of them can match.
    """
    limit = 5
    contenders = 20

    results = await asyncio.gather(
        *(crud.try_claim_registration_seat(limit=limit) for _ in range(contenders))
    )
    granted = sum(1 for r in results if r)

    assert granted == limit, (
        f"{contenders} simultaneous claims against a limit of {limit} granted "
        f"{granted}. The cap is a check-then-act race again."
    )

    async with async_session_maker() as session:
        seats = (
            await session.execute(select(RegistrationCapacity.seats_taken))
        ).scalar_one()
    assert seats == limit, "the counter must match the number of grants"


async def test_released_seat_can_be_claimed_again():
    """A failed registration must not permanently consume capacity."""
    assert await crud.try_claim_registration_seat(limit=1) is True
    assert await crud.try_claim_registration_seat(limit=1) is False

    await crud.release_registration_seat()

    assert await crud.try_claim_registration_seat(limit=1) is True, (
        "a released seat must be reusable, or repeated failures "
        "permanently close the instance"
    )


async def test_release_never_underflows():
    """Releasing more than claimed must clamp at zero, not go negative."""
    for _ in range(3):
        await crud.release_registration_seat()

    async with async_session_maker() as session:
        seats = (
            await session.execute(select(RegistrationCapacity.seats_taken))
        ).scalar_one_or_none()

    assert seats is None or seats >= 0, f"seats_taken went negative: {seats}"


async def test_counter_seeds_from_the_real_user_count():
    """An existing deployment must not start its counter at zero.

    A brand-new table is 0, so without seeding the first `limit` registrations
    would be admitted on top of everyone who already has an account.
    """
    from castor.app.db import User

    async with async_session_maker() as session:
        for i in range(3):
            session.add(
                User(
                    email=f"seed-cap-{i}@example.com",
                    hashed_password="x",
                    is_active=True,
                    is_superuser=False,
                    is_verified=True,
                )
            )
        await session.commit()

    real = await crud.get_user_count()
    assert real >= 3

    # The seeded row must not permit admissions below the real user count.
    assert await crud.try_claim_registration_seat(limit=real) is False, (
        "seeding must start at the true user count, so a limit equal to the "
        "existing user count is already full"
    )
