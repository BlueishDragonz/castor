"""Domain helpers used by the FastAPI API + scheduler + Astro front-end.

This module used to also host NiceGUI-only functions (session helpers,
theming, register/login UX). Those were removed in P3-B when the
beaverhabits/frontend/ directory was deleted; the Astro app handles
register/login UX in src/pages/{login,register}.astro and the
httpOnly session cookie is set by src/lib/auth.ts::writeSession.

Surviving functions (still called outside frontend/):
  - dummy_empty_habit_list, dummy_habit_list: seed for new users
  - get_user_habit_list, get_user_habit, remove_user_habit,
    get_or_create_user_habit_list: domain access used by /api/v1/* and circles
  - delete_user_account: /api/v1/account (DELETE)
  - register_user: /auth/register (Google One Tap auto-create + legacy
    trusted-local-email path)
  - get_activated_users, backup_all_users: scheduler (daily backup task)
"""
import datetime
import random
from typing import Sequence

from fastapi import HTTPException

from castor.app import crud
from castor.app.auth import (
    user_create,
    user_archive,
)
from castor.app.crud import get_customer_list, get_user_list
from castor.app.db import User
from castor.configs import settings
from castor.core.backup import backup_to_telegram
from castor.logger import logger
from castor.storage import get_user_dict_storage
from castor.storage.dict import DAY_MASK, DictHabitList
from castor.storage.storage import Habit, HabitList
from castor.utils import generate_short_hash

user_storage = get_user_dict_storage()


def dummy_empty_habit_list() -> DictHabitList:
    return DictHabitList({"habits": []})


def dummy_habit_list(days: list[datetime.date]):
    pick = lambda: random.randint(0, 4) == 0
    items = [
        {
            "id": generate_short_hash(name),
            "name": name,
            "records": [
                {"day": day.strftime(DAY_MASK), "done": True} for day in days if pick()
            ],
        }
        for name in ("Order pizz", "Running", "Table Tennis", "Clean", "Call mom")
    ]
    return DictHabitList({"habits": items})


async def get_user_habit_list(user: User) -> HabitList:
    try:
        return await user_storage.get_user_habit_list(user)
    except Exception:
        raise HTTPException(
            status_code=404,
            detail="The habit list data may be broken or missing, please contact the administrator.",
        )


async def get_user_habit(user: User, habit_id: str) -> Habit:
    habit_list = await get_user_habit_list(user)
    habit = await habit_list.get_habit_by(habit_id)
    if habit is None:
        raise HTTPException(status_code=404, detail="Habit not found")
    return habit


async def remove_user_habit(user: User, habit: Habit) -> None:
    habit_list = await get_user_habit_list(user)
    await habit_list.remove(habit)


async def get_or_create_user_habit_list(user: User, habit_list: HabitList) -> HabitList:
    try:
        return await get_user_habit_list(user)
    except HTTPException:
        logger.warning(f"Failed to load habit list for user {user.email}")

    logger.info(f"Creating dummy habit list for user {user.email}")
    await user_storage.init_user_habit_list(user, habit_list)

    return await get_user_habit_list(user)


async def delete_user_account(user: User) -> None:
    """Delete personal data and retain only an anonymous, disabled tombstone."""
    await user_storage.delete_user_habit_list(user)
    await crud.delete_user_api_token(user)
    await crud.delete_user_identity(user.email)
    await crud.delete_user_owned_data(user)
    await user_archive(user)


async def register_user(email: str, password: str | None = None) -> User:
    logger.info(f"Registering user {email}...")
    user = await user_create(email=email, password=password)
    # Create a dummy habit list for the new users
    days = [datetime.date.today() - datetime.timedelta(days=i) for i in range(30)]
    habit_list = dummy_habit_list(days)
    await get_or_create_user_habit_list(user, habit_list)
    logger.info(f"User {email} registered successfully")
    return user


async def get_activated_users() -> Sequence[User]:
    users = await get_user_list()
    if not settings.ENABLE_PLAN:
        return users

    customers = await get_customer_list()
    emails = [customer.email for customer in customers if customer.activated]
    logger.debug(f"Activated users: {emails}")

    return [user for user in users if user.email in emails]


async def backup_all_users():
    for user in await get_activated_users():
        logger.info(f"Backing up habit list for user {user.email}...")
        habit_list = await get_user_habit_list(user)
        if habit_list is None:
            logger.warning(f"Failed to load habit list for user {user.email}")
            continue

        backup = habit_list.backup
        if not backup.telegram_bot_token or not backup.telegram_chat_id:
            logger.warning(f"User {user.email} has no backup settings")
            continue

        try:
            backup_to_telegram(
                backup.telegram_bot_token, backup.telegram_chat_id, habit_list
            )
        except Exception as e:
            logger.error(f"Failed to backup habit list for user {user.email}: {e}")
        else:
            logger.info(f"Successfully backed up habit list for user {user.email}")
