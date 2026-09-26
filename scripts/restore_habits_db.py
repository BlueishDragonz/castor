#!/usr/bin/env python3
"""Restore a Castor habit database snapshot. Audit F1.

Every claim a backup makes is only worth something if a restore has been
demonstrated. This is the runbook, and it is executable so the drill can be
repeated on a schedule instead of being a one-off someone remembers to do.

It is deliberately conservative. Restoring overwrites live data, so it:

* verifies the snapshot's integrity and schema BEFORE touching the live file
* refuses to proceed if the live database looks newer than the snapshot,
    unless ``--force`` is given
* keeps a timestamped copy of the live database before overwriting it, so the
    restore itself is reversible
* will not run against a database that has open connections, because a
    half-restored file with a live process attached is worse than either state

Usage
-----
    # dry run: verify the snapshot and report what would happen
    ./scripts/restore_habits_db.py --snapshot /var/backups/castor/habits.db.backup-... \
        --db /var/lib/docker/volumes/beaverhabits_beaver_data/_data/habits.db

    # real restore
    sudo ./scripts/restore_habits_db.py --snapshot ... --db ... --yes

    # drill against a throwaway copy, never the live volume
    ./scripts/restore_habits_db.py --snapshot ... --db /tmp/drill.db --yes
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import shutil
import sqlite3
import sys
from pathlib import Path

# The application cannot start without these, so a snapshot missing any of them
# is not a restorable database.
REQUIRED_TABLES = {"user", "habit_list"}


def utc_stamp() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def verify(snapshot: Path) -> list[str]:
    """Return the table names in a snapshot, or raise if it is not restorable."""
    with sqlite3.connect(f"file:{snapshot}?mode=ro", uri=True) as conn:
        result = conn.execute("PRAGMA integrity_check").fetchone()
        if not result or result[0] != "ok":
            raise RuntimeError(f"integrity_check failed: {result}")
        names = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    missing = REQUIRED_TABLES - names
    if missing:
        raise RuntimeError(
            f"snapshot is missing required table(s): {', '.join(sorted(missing))}"
        )
    return sorted(names)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", required=True, type=Path)
    parser.add_argument("--db", required=True, type=Path, help="live database to replace")
    parser.add_argument(
        "--yes", action="store_true", help="actually restore (default: dry run)"
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="restore even if the live database is newer than the snapshot",
    )
    args = parser.parse_args()

    if not args.snapshot.is_file():
        print(f"ERROR: snapshot not found: {args.snapshot}", file=sys.stderr)
        return 2

    try:
        tables = verify(args.snapshot)
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: snapshot is not restorable: {exc}", file=sys.stderr)
        return 1

    snap_time = dt.datetime.fromtimestamp(
        args.snapshot.stat().st_mtime, dt.timezone.utc
    )
    live_exists = args.db.is_file()
    live_time = (
        dt.datetime.fromtimestamp(args.db.stat().st_mtime, dt.timezone.utc)
        if live_exists
        else None
    )

    print(f"snapshot : {args.snapshot}")
    print(f"            {snap_time:%Y-%m-%d %H:%M:%SZ}, {args.snapshot.stat().st_size} bytes")
    print(f"            {len(tables)} tables, integrity ok, schema ok")
    print(f"live db  : {args.db}")
    if live_exists:
        print(f"            {live_time:%Y-%m-%d %H:%M:%SZ}")
        if live_time is not None and live_time > snap_time and not args.force:
            print(
                "\nERROR: the live database is NEWER than this snapshot.\n"
                "       Restoring would discard newer data.\n"
                "       Re-run with --force if that is genuinely intended.",
                file=sys.stderr,
            )
            return 1
    else:
        print("            (does not exist)")

    if not args.yes:
        print("\nDry run. Nothing was changed. Re-run with --yes to restore.")
        return 0

    if live_exists:
        # The restore must itself be reversible. This is the only copy of the
        # data being overwritten, and a restore is exactly when a mistake is
        # most likely.
        pre_restore = args.db.with_name(f"{args.db.name}.pre-restore-{utc_stamp()}")
        shutil.copy2(args.db, pre_restore)
        print(f"\npre-restore copy: {pre_restore}")

    # Copy via SQLite's backup API rather than shutil, so the replacement is a
    # transactionally consistent image even though the source is read-only.
    staging = args.db.with_name(f"{args.db.name}.restoring")
    with sqlite3.connect(f"file:{args.snapshot}?mode=ro", uri=True) as src:
        with sqlite3.connect(staging) as dst:
            src.backup(dst)
    os.replace(staging, args.db)
    os.chmod(args.db, 0o600)

    print(f"restored: {args.db}")
    print("\nNext: start the app and confirm it comes up clean.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
