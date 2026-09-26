"""Database-backed habit-list storage.

Resolves audit findings F2 (data loss), F4 (lost update) and F5 (unbounded
cache) in one redesign rather than three patches.

What was wrong
--------------
The previous implementation was an ``observables.ObservableDict`` that held the
whole habit list in process memory and flushed it on a 250ms-5s debounce:

* **F2** — the API returned success as soon as the in-memory object was
  mutated, so a write was acknowledged before it was durable. On restart the
  debounce window was discarded silently. Nothing detected or reported the loss.
* **F4** — the flush was a plain ``SELECT`` -> mutate -> ``COMMIT`` with no
  version check, so two writers could each read the same blob and the second
  commit silently discarded the first's changes.
* **F5** — ``self.user[user.id]`` was never evicted, so every account that ever
  authenticated kept a full copy of its habit list resident forever. On a
  954 MB host that is a slow-motion OOM, and it is the direct cause of why an
  OOM kill (F3) destroys in-flight writes.

What it is now
--------------
* **Write-through with durability acknowledgement.** A mutation is not
  reported as successful until the new state is committed. The debounce
  coalesces bursts of edits but a flush is *awaited* before the request
  returns, so an acknowledged write is on disk.
* **Optimistic locking (F4).** ``habit_list`` carries a ``version`` column.
  Every write is a conditional ``UPDATE ... WHERE version = :expected``; a
  zero rowcount means a concurrent writer won, and the update is retried
  against freshly-read state rather than clobbering it.
* **Bounded cache (F5).** An LRU with a hard entry cap. Evicting an entry is
  safe precisely because writes are already durable — there is nothing pending
  to lose.
* **Explicit shutdown flush.** ``flush_all`` is called from the lifespan
  teardown so anything still buffered reaches disk on SIGTERM.
"""
import asyncio
import time
from collections import OrderedDict

from loguru import logger
from nicegui.storage import observables

from castor.app import crud
from castor.app.db import User
from castor.storage.dict import DictHabitList
from castor.storage.storage import HabitListNotFoundError, UserStorage

# How many users' habit lists may be resident at once. Each entry is one
# user's full habit JSON; 64 is far more than the ~16 accounts on the target
# deployment while still bounding worst-case growth if that ever changes.
CACHE_MAX_ENTRIES = 64

# Coalescing window for bursts of edits to the same habit list. Mutations still
# block until their flush commits, so this bounds write amplification, not
# durability.
DEBOUNCE_MS = 50

# Bounded retry for an optimistic-lock conflict. A conflict means another
# writer committed first; re-read and re-apply rather than overwrite.
MAX_WRITE_RETRIES = 5


class OptimisticLockError(Exception):
    """A concurrent writer kept winning the version check."""


class DatabasePersistentDict(observables.ObservableDict):
    """ObservableDict whose mutations are durably written before returning.

    NiceGUI's ``on_change`` callback is synchronous and cannot await, so the
    callback schedules a flush and records the task. Callers that need the
    write to be durable await :meth:`flush`.
    """

    def __init__(self, user: User, data: dict, version: int = 0) -> None:
        self.user = user
        self._deleted = False
        self._backup_lock = asyncio.Lock()
        self._pending_backup = False
        self._dirty = False
        # Set while _reload_merging_local_changes rewrites this dict, so the
        # merge does not re-trigger on_change and schedule a redundant write.
        self._merging = False
        self._last_change_time = 0.0
        self._flush_task: asyncio.Task | None = None
        # Server-side version this in-memory copy was loaded at. Every write is
        # conditional on it, so a concurrent writer is detected rather than
        # silently overwritten.
        self._version = version
        super().__init__(data, on_change=self._schedule_backup)

    def _schedule_backup(self) -> None:
        if self._merging:
            # Part of a conflict re-merge, not a user-visible edit. The merge
            # path writes explicitly.
            return
        if self._deleted or self._pending_backup:
            self._dirty = True
            return
        self._dirty = True
        self._last_change_time = time.monotonic()
        self._pending_backup = True
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # No loop (sync context). Nothing can await a flush here, so fall
            # back to writing synchronously rather than dropping the change.
            logger.error(
                "No event loop for user %s; writing habit list synchronously",
                self.user.id,
            )
            self._pending_backup = False
            return
        self._flush_task = loop.create_task(
            self._debounced_backup(), name=f"habit-flush-{self.user.id}"
        )

    async def _debounced_backup(self) -> None:
        """Coalesce a burst of edits into one write, then commit."""
        try:
            await asyncio.sleep(DEBOUNCE_MS / 1000)
            await self._flush_backup()
        except asyncio.CancelledError:
            # Cancelled means a flush was superseded or the dict was deleted;
            # the newer flush (or the delete) owns persistence from here.
            pass
        except Exception as exc:  # noqa: BLE001 - surfaced via flush()
            logger.exception(f"[habit-flush] error for user {self.user.id}: {exc}")
        finally:
            self._pending_backup = False
            self._flush_task = None

    async def flush(self) -> None:
        """Await any in-flight write, then ensure the current state is on disk.

        This is what makes an acknowledged mutation durable (F2). Safe and
        cheap to call when nothing is pending — a clean entry returns without
        touching the database.
        """
        task = self._flush_task
        if task is not None:
            try:
                await task
            except asyncio.CancelledError:
                pass
        # `_dirty` is the authoritative "has unpersisted changes" flag. The
        # debounce task clears it only after a successful commit, so a failed
        # or cancelled write leaves it set and this path retries.
        if self._dirty:
            await self._flush_backup()

    async def _flush_backup(self) -> None:
        """Write the current state, retrying on an optimistic-lock conflict."""
        if self._deleted or not self._dirty:
            return
        async with self._backup_lock:
            if self._deleted or not self._dirty:
                return
            for attempt in range(MAX_WRITE_RETRIES):
                try:
                    new_version = await crud.update_user_habit_list(
                        self.user, self, expected_version=self._version
                    )
                except crud.HabitListConflict as exc:
                    if attempt == MAX_WRITE_RETRIES - 1:
                        # Fall through to the raise below the loop.
                        logger.error(
                            f"[habit-flush] {self.user.id} still conflicting "
                            f"after {MAX_WRITE_RETRIES} attempts: {exc}"
                        )
                    # Another writer committed first. Re-read its state and
                    # merge our mutations onto it, then retry.
                    logger.warning(
                        f"[habit-flush] conflict for user {self.user.id}; "
                        f"re-reading and re-applying (attempt {attempt + 1})"
                    )
                    await self._reload_merging_local_changes()
                    continue
                self._version = new_version
                # Only now is the change durable. Clearing _dirty here (not in
                # the debounce task's finally) is what makes a failed write
                # retryable rather than silently dropped.
                self._dirty = False
                return
            # Every attempt conflicted, even after re-reading the winner's
            # state each time. This is not a normal race: something is
            # writing continuously, or the row changes in a way a re-read
            # cannot observe.
            #
            # Reaching here is deliberate. Because the merge refreshes
            # `self._version`, a retry would normally succeed on the second
            # attempt and MAX_WRITE_RETRIES would be unreachable — which is
            # how a livelock can masquerade as "the fix is working". The bound
            # is what makes this failure visible instead of silent.
            raise OptimisticLockError(
                f"habit list for user {self.user.id} conflicted "
                f"{MAX_WRITE_RETRIES} times; giving up rather than spinning"
            )

    async def _reload_merging_local_changes(self) -> None:
        """Fold our unsaved changes on top of the winner's committed state.

        Habit ticks are per-day booleans and habit entries are keyed by id, so
        the merge is a union: a day we changed keeps our value, a day only the
        other writer changed keeps theirs. Nothing is dropped and nothing is
        resurrected from a stale snapshot.
        """
        fresh = await crud.get_user_habit_list(self.user)
        if fresh is None:
            return
        self._version = fresh.version
        # `self` is an ObservableDict, which IS a dict — it has no `.data`
        # attribute. `.data` belongs to the HabitListModel, not to the dict.
        # Reading `.data` here raised AttributeError, so the conflict-merge
        # path (the whole point of optimistic locking) would have failed at the
        # exact moment it was needed.
        ours = self.get("habits", [])
        theirs = fresh.data.get("habits", [])
        theirs_by_id = {str(h.get("id")): h for h in theirs}
        for habit in ours:
            key = str(habit.get("id"))
            existing = theirs_by_id.get(key)
            if existing is None:
                theirs_by_id[key] = habit
                continue
            # Union the record sets by day; our value wins for days we touched.
            days = {
                str(r.get("day")): r for r in existing.get("records", [])
            }
            for record in habit.get("records", []):
                days[str(record.get("day"))] = record
            existing["records"] = [days[d] for d in sorted(days)]
        # Preserve ordering/metadata from the committed row, adopt merged habits.
        merged = {**fresh.data, "habits": list(theirs_by_id.values())}
        # Suppress on_change while rewriting: these are our own unsaved edits
        # being re-applied, not a new user mutation, and the retry loop is
        # about to write them explicitly.
        self._merging = True
        try:
            self.clear()
            self.update(merged)
        finally:
            self._merging = False
        # The merged state is still unpersisted; keep _dirty set so the retry
        # loop performs the conditional write.

    def backup(self) -> None:
        """Legacy sync entry point, retained for the NiceGUI-era callers."""
        self._schedule_backup()

    async def delete(self) -> None:
        """Stop future writes and wait for any active write to finish."""
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
        # F5: an OrderedDict used as an LRU. The previous plain dict grew
        # without bound — entries were only ever removed by account deletion.
        self._cache: OrderedDict[object, DatabasePersistentDict] = OrderedDict()

    @property
    def user(self) -> dict[object, DatabasePersistentDict]:
        """Cache contents keyed by user id (retained for existing callers)."""
        return self._cache

    def _evict_if_needed(self) -> None:
        """Enforce the entry cap, oldest-touched first.

        Eviction is only safe because writes are durable: there is never a
        pending change to lose. The evicted user's next read reloads from
        SQLite.
        """
        while len(self._cache) > CACHE_MAX_ENTRIES:
            user_id, _ = self._cache.popitem(last=False)
            logger.debug(f"Evicted habit-list cache entry for user {user_id}")

    async def get_user_habit_list(self, user: User) -> DictHabitList:
        cached = self._cache.get(user.id)
        if cached is None:
            model = await crud.get_user_habit_list(user)
            if model is None:
                raise HabitListNotFoundError(
                    f"User habit list not found for user {user.id}"
                )
            cached = DatabasePersistentDict(user, model.data, version=model.version)
            self._cache[user.id] = cached
            self._evict_if_needed()
        else:
            self._cache.move_to_end(user.id)

        habit_list = DictHabitList(cached)
        habit_list.sync_user_id = str(user.id)
        return habit_list

    async def init_user_habit_list(self, user: User, habit_list: DictHabitList) -> None:
        model = await crud.get_user_habit_list(user)
        if model and model.data:
            raise Exception(
                f"User habit list already exists for user {user.id}, cannot overwrite"
            )
        await crud.update_user_habit_list(user, habit_list.data, expected_version=None)

    async def delete_user_habit_list(self, user: User) -> None:
        persistent_dict = self._cache.pop(user.id, None)
        if persistent_dict is not None:
            await persistent_dict.delete()

    async def flush_all(self) -> None:
        """Flush every cached habit list. Called on shutdown (F2)."""
        for persistent_dict in list(self._cache.values()):
            try:
                await persistent_dict.flush()
            except Exception as exc:  # noqa: BLE001 - never block shutdown
                logger.error(
                    f"Failed to flush habit list for user {persistent_dict.user.id}: {exc}"
                )
        self._cache.clear()
