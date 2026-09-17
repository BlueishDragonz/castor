"""Independent SQLite integrity monitor; no backup or GUI imports.

Wire daily_integrity_task() unconditionally at startup (default: immediate check
then every 24 hours), retain its task, cancel AND await it at shutdown. An explicit
URL may be supplied; otherwise the configured db.engine.url is used. Default
status file is adjacent to the SQLite database, on the same persistent volume.
Non-SQLite and memory databases are skipped, never probed via a fallback file.
The JSON result intentionally excludes paths, SQL diagnostics and exception text.
"""
import asyncio
import json
import math
import os
import sqlite3
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote, urlsplit

from loguru import logger
from sqlalchemy.engine import URL, make_url

DEFAULT_INTERVAL_SECONDS = 24 * 60 * 60


def _target(database_url: str | URL) -> tuple[Path | None, str]:
    url = make_url(database_url)
    if url.get_backend_name() != "sqlite":
        return None, "non_sqlite"
    database = url.database
    if not database or database == ":memory:" or url.query.get("mode") == "memory":
        return None, "memory_database"
    if url.query.get("vfs"):
        raise ValueError("Custom SQLite VFS is not supported")
    if database.startswith("file:"):
        if str(url.query.get("uri", "false")).lower() != "true":
            # A literal filename beginning file: is not a SQLite URI without uri=true.
            return Path(database).absolute(), "sqlite"
        parsed = urlsplit(database)
        if parsed.netloc not in ("", "localhost"):
            raise ValueError("Unsupported SQLite URI authority")
        database = unquote(parsed.path)
        if database == ":memory:" or not database:
            return None, "memory_database"
    return Path(database).absolute(), "sqlite"


def _persist(path: Path, result: dict) -> None:
    """Same-directory atomic replace, private permissions, fsync file and directory."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        fd, temporary = tempfile.mkstemp(prefix=".integrity-", suffix=".tmp", dir=path.parent)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(result, stream, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        temporary = None
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if temporary is not None:
            os.unlink(temporary)


def _check(database_url, result_path, cancelled: threading.Event) -> dict:
    result = {
        "schema_version": 1,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "status": "error", "reason": "check_error", "problem_count": 0,
        "persisted": True,
    }
    target = None
    status_path = Path(result_path) if result_path is not None else None
    try:
        target, reason = _target(database_url)
    except Exception:
        # No URL or exception repr: credentials can be embedded in database URLs.
        result["reason"] = "configuration_error"
    else:
        if target is None:
            result.update(status="skipped", reason=reason)
        else:
            if status_path is None:
                status_path = target.with_name(target.name + ".integrity.json")
            # Misconfiguration must never atomically replace the database or its WAL.
            protected = [target, *(Path(str(target) + suffix) for suffix in ("-wal", "-shm", "-journal"))]
            if any(status_path.resolve() == path.resolve() for path in protected):
                raise ValueError("Integrity result must not overwrite SQLite files")
            connection = None
            deadline = time.monotonic() + 60
            try:
                # Fresh URI ignores write/immutable/nolock flags; mode=ro refuses creation.
                connection = sqlite3.connect(target.as_uri() + "?mode=ro", uri=True, timeout=5)
                connection.set_progress_handler(
                    lambda: int(cancelled.is_set() or time.monotonic() >= deadline), 1000,
                )
                connection.execute("PRAGMA query_only=ON")
                rows = connection.execute("PRAGMA integrity_check").fetchall()
                if rows == [("ok",)]:
                    result.update(status="ok", reason="integrity_ok")
                else:
                    result.update(status="failed", reason="integrity_failed", problem_count=len(rows))
            except Exception:
                result.update(status="error", reason="check_error")
            finally:
                if connection is not None:
                    connection.close()
    if cancelled.is_set():
        return result  # Shutdown must not publish an interrupted scan as corruption.
    if result["status"] in ("error", "failed"):
        logger.error("SQLite integrity check unsuccessful: {}", result["reason"])
    if status_path is None:
        # Only for non-file databases or invalid configuration; never a DB fallback.
        from beaverhabits.configs import USER_DATA_FOLDER
        status_path = Path(USER_DATA_FOLDER) / "sqlite-integrity.json"
    try:
        _persist(status_path, result)
    except Exception:
        result["persisted"] = False
        logger.error("SQLite integrity result persistence failed")
    return result


async def check_sqlite_integrity(
    database_url: str | URL | None = None, *, result_path: str | Path | None = None,
) -> dict:
    """Run and persist a bounded, read-only scan entirely outside the event loop.

Cancellation signals SQLite's progress handler and joins the worker before
propagating CancelledError. No detached worker continues during normal shutdown.
"""
    if database_url is None:
        from beaverhabits.app.db import engine
        database_url = engine.url
    cancelled = threading.Event()
    worker = asyncio.create_task(asyncio.to_thread(_check, database_url, result_path, cancelled))
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError:
        cancelled.set()
        # Shield from repeated cancellation while the dedicated connection closes.
        while not worker.done():
            try:
                await asyncio.shield(worker)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        if worker.done() and not worker.cancelled():
            worker.exception()  # Retrieve any worker failure without leaking details.
        raise


async def daily_integrity_task(
    database_url: str | URL | None = None, *, result_path: str | Path | None = None,
    interval_seconds: float = DEFAULT_INTERVAL_SECONDS,
) -> None:
    """Immediate and periodic checks, independent of ENABLE_DAILY_BACKUP.

All errors are logged without exception details and retried next interval.
Audit retention is separate: parent may schedule prune_audit_events alongside.
"""
    if (isinstance(interval_seconds, bool) or not isinstance(interval_seconds, (int, float))
            or not math.isfinite(interval_seconds) or interval_seconds <= 0):
        raise ValueError("interval_seconds must be finite and positive")
    while True:
        try:
            await check_sqlite_integrity(database_url, result_path=result_path)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.error("SQLite integrity monitor failed")
        await asyncio.sleep(interval_seconds)
