import asyncio
import time

from loguru import logger
from nicegui import background_tasks, core
from nicegui.storage import observables

from beaverhabits.app import crud
from beaverhabits.app.db import User
from beaverhabits.storage.dict import DictHabitList
from beaverhabits.storage.storage import HabitListNotFoundError, UserStorage


class DatabasePersistentDict(observables.ObservableDict):
    """ObservableDict with debounced database backup.

    Changes are batched: backup runs 250ms after the last mutation,
    with a maximum forced flush after 5 seconds.
    """

    DEBOUNCE_MS = 250
    MAX_FLUSH_MS = 5000

    def __init__(self, user: User, data: dict) -> None:
        self.user = user
        self._deleted = False
        self._backup_lock = asyncio.Lock()
        self._pending_backup = False
        self._last_change_time = 0.0
        self._flush_task: asyncio.Task | None = None
        super().__init__(data, on_change=self._schedule_backup)

    def _schedule_backup(self) -> None:
        if self._deleted:
            return

        now = time.monotonic()
        self._last_change_time = now

        if not self._pending_backup:
            self._pending_backup = True
            # Schedule the debounced backup
            if core.loop and core.loop.is_running():
                self._flush_task = background_tasks.create_lazy(
                    self._debounced_backup(), name=f"debounced-backup-{self.user.email}"
                )
            else:
                logger.error("No event loop for scheduling debounced backup")

    async def _debounced_backup(self) -> None:
        """Wait for debounce window, then flush."""
        try:
            # Wait for debounce period
            await asyncio.sleep(self.DEBOUNCE_MS / 1000)

            # Check if more changes came in during debounce
            elapsed = time.monotonic() - self._last_change_time
            if elapsed < self.DEBOUNCE_MS / 1000:
                # More changes arrived; wait for max flush time
                await asyncio.sleep((self.MAX_FLUSH_MS - self.DEBOUNCE_MS) / 1000)

            await self._flush_backup()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.exception(f"[debounced-backup] error for {self.user.email}: {e}")
        finally:
            self._pending_backup = False
            self._flush_task = None

    async def _flush_backup(self) -> None:
        """Perform the actual database write."""
        async with self._backup_lock:
            if self._deleted:
                return
            try:
                await crud.update_user_habit_list(self.user, self)
            except Exception as e:
                logger.exception(
                    f"[backup] failed to update habit list for user {self.user.email}: {e}"
                )

    def backup(self) -> None:
        """Legacy sync backup - kept for compatibility, now delegates to debounced."""
        self._schedule_backup()

    async def delete(self) -> None:
        """Stop future database backups and wait for any active backup to finish."""
        self._deleted = True
        if self._flush_task:
            self._flush_task.cancel()
            try:
                await self._flush_task
            except asyncio.CancelledError:
                pass
        async with self._backup_lock:
            pass


class UserDatabaseStorage(UserStorage[DictHabitList]):
    def __init__(self) -> None:
        self.user: dict[object, DatabasePersistentDict] = {}

    async def get_user_habit_list(self, user: User) -> DictHabitList:
        if user.id not in self.user:
            user_habit_list = await crud.get_user_habit_list(user)
            if user_habit_list is None:
                raise HabitListNotFoundError(
                    f"User habit list not found for user {user.email}"
                )
            self.user[user.id] = DatabasePersistentDict(user, user_habit_list.data)

        habit_list = DictHabitList(self.user[user.id])
        habit_list.sync_user_id = str(user.id)
        return habit_list

    async def init_user_habit_list(self, user: User, habit_list: DictHabitList) -> None:
        user_habit_list = await crud.get_user_habit_list(user)
        if user_habit_list and user_habit_list.data:
            raise Exception(
                f"User habit list already exists for user {user.email}, cannot overwrite"
            )

        await crud.update_user_habit_list(user, habit_list.data)

    async def delete_user_habit_list(self, user: User) -> None:
        persistent_dict = self.user.pop(user.id, None)
        if persistent_dict is not None:
            await persistent_dict.delete()
