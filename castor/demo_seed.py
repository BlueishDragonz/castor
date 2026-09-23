"""
Demo account seeding for local development.

The demo account (`demo@castor.example.com / DemoPass1234!`) was originally
seeded by a one-shot script. Something has been wiping it every few minutes
during development sessions — the cause is still under investigation (the
backend logs show no DELETE on the demo row, but the user vanishes
periodically anyway). Rather than spend another round hunting the source,
we give the demo account three lines of defence:

  1. `seed_demo_account()` is callable from anywhere that needs the demo
     user. It's idempotent — if the user already exists it just verifies
     the password still matches and that the 5 demo habits exist.

  2. `init_demo_seed_task()` is a startup hook in castor/main.py that runs
     `seed_demo_account()` once after the DB is ready. Gated to DEBUG mode
     so production never auto-seeds a demo account.

  3. The HTTP endpoint `POST /dev/reseed-demo` is also gated to DEBUG.
     Useful when the user notices the demo is gone and wants to recover
     without restarting the server.

The credentials are deliberately weak (`DemoPass1234!`) because this is a
local-only dev affordance, not a production account. The endpoint and the
startup hook refuse to run unless DEBUG is on, so a misconfigured deploy
can never auto-seed a demo account.
"""
from __future__ import annotations

import asyncio
import datetime
import uuid
from typing import Optional

from fastapi_users.password import PasswordHelper
from sqlalchemy import select, update

from castor import views as app_views
from castor.app import db as app_db
from castor.app.db import User
from castor.configs import settings
from castor.logger import logger
from castor.storage.storage import HabitFrequency


DEMO_EMAIL = "demo@castor.example.com"
DEMO_PASSWORD = "DemoPass1234!"


# Habit seeds: (name, tags, optional period). Period uses the backend's
# {period_type, period_count, target_count} shape (see api.py UpdateHabitPeriod).
# period_type is one of "D" / "W" / "M" / "Y".
HABIT_SEEDS: list[dict] = [
    {"name": "Drink water", "tags": ["health", "daily"]},
    {
        "name": "Walk 20 min",
        "tags": ["fitness", "daily"],
        "period": {"period_type": "D", "period_count": 14, "target_count": 1},
    },
    {
        "name": "Read 30 min",
        "tags": ["learning", "daily"],
        "period": {"period_type": "D", "period_count": 30, "target_count": 1},
    },
    {"name": "Write journal", "tags": ["mindfulness", "weekly"]},
    {"name": "Stretch & breathe", "tags": ["health", "mindfulness"]},
]


def _date_set(today: datetime.date, profile: str) -> set[datetime.date]:
    """Generate a set of dates matching the requested density profile."""
    if profile == "full":
        return {today - datetime.timedelta(days=i) for i in range(21)}
    if profile == "current":
        recent = {today - datetime.timedelta(days=i) for i in range(4)}
        gap = {today - datetime.timedelta(days=i) for i in range(5, 13)}
        return recent | gap
    if profile == "weekday":
        return {
            today - datetime.timedelta(days=i)
            for i in range(14)
            if (today - datetime.timedelta(days=i)).weekday() < 5
        }
    if profile == "sparse":
        return {today, today - datetime.timedelta(days=3)}
    if profile == "very_sparse":
        return {today, today - datetime.timedelta(days=2), today - datetime.timedelta(days=5)}
    raise ValueError(f"unknown profile: {profile}")


# Profile per habit seed index.
PROFILES = ["full", "current", "weekday", "sparse", "very_sparse"]


async def _find_demo_user() -> Optional[User]:
    async with app_db.async_session_maker() as session:
        res = await session.execute(select(User).where(User.email == DEMO_EMAIL))
        return res.scalar_one_or_none()


async def _ensure_demo_user() -> User:
    """Make sure demo@castor.example.com exists with the canonical password.

    Returns the demo User row (created or fetched).
    """
    existing = await _find_demo_user()
    if existing is not None:
        # Verify password still works. If not (created with a different
        # password in some prior session), rewrite it. Bump token_version
        # so any cached JWT for this user is invalidated.
        ph = PasswordHelper()
        ok, _ = ph.verify_and_update(DEMO_PASSWORD, existing.hashed_password)
        if not ok:
            logger.warning("Demo user exists but password differs — resetting to canonical")
            new_hash = ph.hash(DEMO_PASSWORD)
            async with app_db.async_session_maker() as session:
                await session.execute(
                    update(User)
                    .where(User.id == existing.id)
                    .values(
                        hashed_password=new_hash,
                        token_version=User.token_version + 1,
                    )
                )
                await session.commit()
        return existing

    ph = PasswordHelper()
    new_hash = ph.hash(DEMO_PASSWORD)
    new_user = User(
        id=uuid.uuid4(),
        email=DEMO_EMAIL,
        hashed_password=new_hash,
        is_active=True,
        is_verified=True,  # demo account — no email verification step needed
    )
    async with app_db.async_session_maker() as session:
        session.add(new_user)
        await session.commit()
        await session.refresh(new_user)
    logger.info("Created demo account {}", DEMO_EMAIL)
    return new_user


async def _ensure_habit_list(user: User) -> int:
    """Make sure the demo user has a habit list with the 5 demo habits.

    Returns the count of new habits created (0 if everything was already there).
    """
    habit_list = await app_views.get_or_create_user_habit_list(
        user, app_views.dummy_empty_habit_list()
    )

    existing_by_name = {h.name: h for h in habit_list.habits}
    new_habit_count = 0

    for seed in HABIT_SEEDS:
        name = seed["name"]
        if name in existing_by_name:
            continue
        hid = await habit_list.add(name)
        if seed.get("tags"):
            habit_obj = next(h for h in habit_list.habits if h.id == hid)
            habit_obj.tags = seed["tags"]
        if seed.get("period"):
            habit_obj = next(h for h in habit_list.habits if h.id == hid)
            habit_obj.period = HabitFrequency(**seed["period"])
        new_habit_count += 1

    # Re-fetch the habit list so newly-added habits are reflected.
    habit_list = await app_views.get_or_create_user_habit_list(
        user, app_views.dummy_empty_habit_list()
    )

    # Tick history — add ticks for dates that don't already have a done record.
    today = datetime.date.today()
    for idx, seed in enumerate(HABIT_SEEDS):
        habit = next(h for h in habit_list.habits if h.name == seed["name"])
        existing_done_dates = set()
        for r in habit.records:
            done = getattr(r, "done", False)
            day = getattr(r, "day", None)
            if done and day:
                existing_done_dates.add(day)

        target = _date_set(today, PROFILES[idx])
        for d in target:
            d_str = d.strftime("%Y-%m-%d")  # DictHabit.tick expects ISO date
            if d_str in existing_done_dates:
                continue
            await habit.tick(d, True, text=None)

    if new_habit_count:
        logger.info("Demo habit list: created {} new habits", new_habit_count)
    return new_habit_count


async def seed_demo_account() -> dict:
    """Make sure the demo account + habit list exist.

    Returns a status dict for the /dev/reseed-demo endpoint.
    """
    user = await _ensure_demo_user()
    new_habits = await _ensure_habit_list(user)
    return {
        "ok": True,
        "email": DEMO_EMAIL,
        "user_id": str(user.id),
        "habits_created": new_habits,
    }


async def init_demo_seed_task() -> None:
    """Startup hook: call seed_demo_account once after the DB is ready.

    Runs in dev environments only (settings.is_dev() = True). Production
    (ENV != "dev") never auto-seeds a demo account.
    """
    if not settings.is_dev():
        logger.debug("Demo seed skipped (not dev)")
        return
    # Yield to the loop so the app can start serving while we seed.
    await asyncio.sleep(0.5)
    try:
        result = await seed_demo_account()
        logger.info("Demo seed on startup: {}", result)
    except Exception:
        logger.exception("Demo seed on startup failed")
