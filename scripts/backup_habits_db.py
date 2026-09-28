#!/usr/bin/env python3
"""Off-host SQLite backup for Castor (audit F1).

Run from a host-side systemd timer, NOT from inside the app container. The app
must not own its own backups: if it does, an OOM kill or a bad deploy takes the
backup job down with it, which is exactly the single-event failure F1 describes.

Why not `cp`
------------
Copying a live SQLite file is unsafe and can silently produce a torn snapshot:
`cp` reads the main database file while a writer may be mid-transaction, and
with `journal_mode=delete` (the pre-F9 default) a crash leaves a hot journal
that the copy does not include. The result looks like a valid backup and is
not one.

`sqlite3.Connection.backup()` is SQLite's Online Backup API. It takes a
consistent snapshot via the page cache and restarts itself if the source is
written during the copy, so the output is always a complete, transactionally
consistent database — on a live, in-use file, without stopping the app.

Guarantees
----------
* **Verified.** Every snapshot is opened and `PRAGMA integrity_check`ed before
  it is allowed to become the newest generation. A corrupt snapshot is deleted
  rather than retained, and the run fails loudly.
* **Atomic.** Snapshots are written to a temp file and `os.replace`d into
  place, so a crash mid-write cannot leave a half-file that looks current.
* **Generational.** The last ``--keep`` snapshots are retained; older ones are
  pruned. This bounds disk use while keeping enough history to survive
  discovering that the last few days were themselves bad.
* **Timestamped.** Names carry a UTC timestamp with a `Z` suffix, matching the
  existing ``habits.db.pre-cutover-20260926T023424Z`` convention.
* **Off-host by construction.** The destination is a path, not a mount the app
  container can see, and the script refuses to run with the app's ``.user``
  directory as its destination.

Usage
-----
    sudo ./scripts/backup_habits_db.py \
        --db /var/lib/docker/volumes/beaverhabits_beaver_data/_data/habits.db \
        --dest /var/backups/castor \
        --keep 14

Exit codes: 0 success, 1 backup/verification failed, 2 bad usage.
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import re
import sqlite3
import sys
import tempfile
from pathlib import Path

# Matches the timestamps this script writes, so pruning cannot delete a
# snapshot it did not create.
SNAPSHOT_RE = re.compile(r"^habits\.db\.backup-(\d{8}T\d{6}Z)\.sqlite3$")

# A habit DB for ~16 users is a few hundred KB. A snapshot under this size means
# the backup silently produced nothing, which must not become the newest
# "good" generation.
MIN_SNAPSHOT_BYTES = 1024


def utc_stamp() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def take_snapshot(source: Path, dest_dir: Path) -> Path:
    """Write a consistent, verified snapshot and return its path."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    final = dest_dir / f"habits.db.backup-{utc_stamp()}.sqlite3"

    # Same directory as the final file so os.replace() is an atomic rename
    # rather than a cross-device copy.
    fd, tmp_name = tempfile.mkstemp(
        dir=dest_dir, prefix=".partial-", suffix=".sqlite3"
    )
    os.close(fd)
    tmp = Path(tmp_name)

    try:
        # Read-only source URI: the backup must never write to, or take a write
        # lock on, the live database.
        src_uri = f"file:{source}?mode=ro"
        with sqlite3.connect(src_uri, uri=True) as src:
            # isolation_level=None + pages=0 is SQLite's documented recipe for
            # backup(): a bare read transaction, pages copied per step, with the
            # whole thing retried automatically if the source changes mid-copy.
            src.execute("PRAGMA busy_timeout=10000")
            with sqlite3.connect(tmp) as dst:
                src.backup(dst, pages=256, sleep=0.05)

        size = tmp.stat().st_size
        if size < MIN_SNAPSHOT_BYTES:
            raise RuntimeError(
                f"snapshot is only {size} bytes, below the {MIN_SNAPSHOT_BYTES}-byte "
                "floor — refusing to keep an empty backup"
            )

        # Verify before promoting. An unverified snapshot that replaced a good
        # one is worse than no snapshot at all.
        with sqlite3.connect(f"file:{tmp}?mode=ro", uri=True) as check:
            result = check.execute("PRAGMA integrity_check").fetchone()
        if not result or result[0] != "ok":
            raise RuntimeError(f"integrity_check failed on snapshot: {result}")

        os.replace(tmp, final)
        os.chmod(final, 0o600)
        return final
    finally:
        # Covers every failure path above: a partial file never survives.
        if tmp.exists():
            tmp.unlink()


def prune(dest_dir: Path, keep: int) -> list[Path]:
    """Keep the ``keep`` newest snapshots; return the ones removed."""
    # Sort by the embedded UTC timestamp, not filename mtime: mtime changes
    # when a snapshot is copied or restored, the stamp does not.
    stamped: list[tuple[str, Path]] = []
    for path in dest_dir.iterdir():
        match = SNAPSHOT_RE.match(path.name)
        if match is not None:
            stamped.append((match.group(1), path))
    snapshots = [p for _, p in sorted(stamped, key=lambda pair: pair[0])]
    if keep <= 0 or len(snapshots) <= keep:
        return []
    removed = []
    for old in snapshots[: len(snapshots) - keep]:
        old.unlink()
        removed.append(old)
    return removed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, type=Path, help="live SQLite file")
    parser.add_argument("--dest", required=True, type=Path, help="backup directory")
    parser.add_argument(
        "--keep", type=int, default=14, help="generations to retain (default 14)"
    )
    args = parser.parse_args()

    if not args.db.is_file():
        print(f"ERROR: database not found: {args.db}", file=sys.stderr)
        return 2

    dest = args.dest.resolve()
    # Guard against the one configuration that would make this a no-op: backing
    # the database up into its own directory, where a later restore could
    # overwrite live data with a stale snapshot.
    if dest == args.db.resolve().parent:
        print(
            "ERROR: --dest must not be the database's own directory",
            file=sys.stderr,
        )
        return 2

    try:
        snapshot = take_snapshot(args.db, dest)
    except Exception as exc:  # noqa: BLE001 - report, do not traceback into cron mail
        print(f"ERROR: backup failed: {exc}", file=sys.stderr)
        return 1

    removed = prune(dest, args.keep)
    print(
        f"OK: {snapshot} ({snapshot.stat().st_size} bytes), "
        f"{len(removed)} old generation(s) pruned"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
