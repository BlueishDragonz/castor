"""Backup and restore drill for the F1 host-side backup (scripts/backup_habits_db.py).

A backup that has never been restored is a hypothesis, not a safety net. This
test performs a full round trip against a real SQLite database:

    write data -> snapshot -> mutate the source -> restore the snapshot
    -> assert the original data is back, byte-for-byte on the row contents

It also asserts the properties the production script depends on:

* a snapshot taken from a *live, in-use* database is consistent
  (SQLite's Online Backup API, not `cp`)
* a corrupt source fails the run loudly and leaves no partial snapshot behind
* an empty/too-small snapshot is refused rather than kept
* retention keeps exactly N generations, newest first
* the destination may not be the database's own directory

Each test uses a real on-disk SQLite file rather than a mock, because the
behaviour under test *is* SQLite's file-level behaviour.
"""
import datetime as dt
import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "backup_habits_db.py"
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import backup_habits_db as backup  # noqa: E402


@pytest.fixture
def live_db():
    """A real SQLite file with a realistic amount of data."""
    tmp = tempfile.TemporaryDirectory()
    path = Path(tmp.name) / "habits.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE habit (id INTEGER PRIMARY KEY, payload TEXT)")
    # Enough rows to exceed MIN_SNAPSHOT_BYTES comfortably.
    conn.executemany(
        "INSERT INTO habit (payload) VALUES (?)",
        [(f"habit-payload-{i}" * 40,) for i in range(200)],
    )
    conn.commit()
    conn.close()
    yield path
    tmp.cleanup()


def rows(db_path: Path) -> list[str]:
    with sqlite3.connect(db_path) as conn:
        return [r[0] for r in conn.execute("SELECT payload FROM habit ORDER BY id")]


def run_script(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_snapshot_preserves_data(live_db, tmp_path):
    """A snapshot contains exactly what the source had."""
    original = rows(live_db)
    snapshot = backup.take_snapshot(live_db, tmp_path / "backups")
    assert rows(snapshot) == original


def test_snapshot_is_consistent_on_a_live_database(live_db, tmp_path):
    """The snapshot is taken while a second connection has the DB open.

    `cp` would be unsafe here; the Online Backup API must still yield a
    database that passes integrity_check and is readable.
    """
    writer = sqlite3.connect(live_db)
    try:
        # WAL plus a concurrent connection is the realistic production shape.
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("INSERT INTO habit (payload) VALUES ('written-during-backup')")
        writer.commit()

        snapshot = backup.take_snapshot(live_db, tmp_path / "backups")
        with sqlite3.connect(snapshot) as check:
            assert check.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        writer.close()

    # The snapshot is a valid database regardless of the concurrent write.
    assert "written-during-backup" in rows(snapshot)


def test_restore_round_trip(live_db, tmp_path):
    """THE POINT OF A BACKUP: restore it and get the original data back."""
    before = rows(live_db)
    snapshot = backup.take_snapshot(live_db, tmp_path / "backups")

    # Lose the data, as a bad deploy or a corrupt file would.
    with sqlite3.connect(live_db) as conn:
        conn.execute("DELETE FROM habit")
        conn.commit()
    assert rows(live_db) == []

    # Restore (recover the snapshot over the live file).
    restored = tmp_path / "restored.db"
    with sqlite3.connect(f"file:{snapshot}?mode=ro", uri=True) as src:
        with sqlite3.connect(restored) as dst:
            src.backup(dst)

    assert rows(restored) == before


def test_corrupt_source_fails_without_leaving_a_partial(live_db, tmp_path):
    """A corrupt source must fail loudly, not leave a plausible-looking file."""
    dest = tmp_path / "backups"
    live_db.write_bytes(b"this is definitely not a SQLite database" * 100)

    result = run_script(
        "--db", str(live_db), "--dest", str(dest), "--keep", "3"
    )
    assert result.returncode == 1, result.stderr
    assert "ERROR" in result.stderr
    # Nothing left behind that a later run could mistake for a good snapshot.
    assert list(dest.glob("habits.db.backup-*.sqlite3")) == []
    assert list(dest.glob(".partial-*")) == []


def test_too_small_snapshot_is_refused(live_db, tmp_path):
    """An empty result must not become the newest 'good' generation."""
    dest = tmp_path / "backups"
    # Shrink the floor below what this database can produce, so the guard is
    # what actually rejects it rather than a genuinely empty file.
    original_floor = backup.MIN_SNAPSHOT_BYTES
    backup.MIN_SNAPSHOT_BYTES = 1024 * 1024 * 64
    try:
        with pytest.raises(RuntimeError, match="below the"):
            backup.take_snapshot(live_db, dest)
    finally:
        backup.MIN_SNAPSHOT_BYTES = original_floor
    assert list(dest.glob("habits.db.backup-*.sqlite3")) == []


def test_retention_keeps_exactly_n_newest(live_db, tmp_path):
    dest = tmp_path / "backups"
    dest.mkdir()
    # Hand-build distinguishable generations rather than sleeping between real
    # backups, so the test is fast and deterministic.
    stamps = [
        "20260101T000000Z",
        "20260102T000000Z",
        "20260103T000000Z",
        "20260104T000000Z",
        "20260105T000000Z",
    ]
    for stamp in stamps:
        (dest / f"habits.db.backup-{stamp}.sqlite3").write_bytes(b"x")

    # A non-snapshot file must survive pruning untouched.
    keeper = dest / "habits.db.pre-cutover-20260926T023424Z"
    keeper.write_bytes(b"keep me")

    removed = backup.prune(dest, keep=3)

    assert len(removed) == 2
    remaining = {p.name for p in dest.glob("habits.db.backup-*.sqlite3")}
    assert remaining == {
        "habits.db.backup-20260103T000000Z.sqlite3",
        "habits.db.backup-20260104T000000Z.sqlite3",
        "habits.db.backup-20260105T000000Z.sqlite3",
    }
    # The manually-placed legacy snapshot is not ours to delete.
    assert keeper.exists()


def test_script_refuses_destination_inside_the_database_directory(live_db):
    """Backing up into the live data dir could overwrite live data on restore."""
    result = run_script(
        "--db", str(live_db), "--dest", str(live_db.parent), "--keep", "3"
    )
    assert result.returncode == 2
    assert "must not be the database's own directory" in result.stderr


def test_script_reports_missing_database(tmp_path):
    result = run_script(
        "--db", str(tmp_path / "nope.db"), "--dest", str(tmp_path / "b"), "--keep", "3"
    )
    assert result.returncode == 2
    assert "not found" in result.stderr


def test_end_to_end_cli_runs_and_prunes(live_db, tmp_path):
    """The systemd timer invokes the CLI, so the CLI path itself is covered."""
    dest = tmp_path / "backups"
    for i in range(3):
        (dest / f"habits.db.backup-2026010{i}T000000Z.sqlite3").parent.mkdir(
            parents=True, exist_ok=True
        )
        (dest / f"habits.db.backup-2026010{i}T000000Z.sqlite3").write_bytes(b"old")

    result = run_script(
        "--db", str(live_db), "--dest", str(dest), "--keep", "3"
    )
    assert result.returncode == 0, result.stderr
    assert "OK:" in result.stdout
    # 3 old + 1 new, keeping 3 => exactly one pruned.
    assert "1 old generation(s) pruned" in result.stdout
    snapshots = list(dest.glob("habits.db.backup-*.sqlite3"))
    assert len(snapshots) == 3
    newest = max(snapshots, key=lambda p: backup.SNAPSHOT_RE.match(p.name).group(1))
    assert rows(newest) == rows(live_db)


def test_snapshot_permissions_are_owner_only(live_db, tmp_path):
    """Habit data is personal; the snapshot must not be world-readable."""
    if os.geteuid() == 0:
        pytest.skip("root bypasses the read-only bit this test relies on")
    snapshot = backup.take_snapshot(live_db, tmp_path / "backups")
    mode = snapshot.stat().st_mode & 0o777
    assert mode == 0o600, f"expected 0600, got {oct(mode)}"


# --------------------------------------------------------------------------
# Restore drill (scripts/restore_habits_db.py)
# --------------------------------------------------------------------------


RESTORE = Path(__file__).resolve().parents[1] / "scripts" / "restore_habits_db.py"
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import restore_habits_db as restore  # noqa: E402

# A snapshot of a bare `habit` table is not a Castor database; the restore
# script must reject it rather than overwrite a live database with it.
SCHEMA = [
    "CREATE TABLE \"user\" (id TEXT PRIMARY KEY, email TEXT)",
    "CREATE TABLE habit_list (id INTEGER PRIMARY KEY, data TEXT)",
    "INSERT INTO \"user\" VALUES ('u1', 'someone@example.com')",
    "INSERT INTO habit_list VALUES (1, '{\"habits\": []}')",
]


def make_castor_db(path: Path, extra_rows: int = 5) -> Path:
    conn = sqlite3.connect(path)
    for stmt in SCHEMA:
        conn.execute(stmt)
    for i in range(extra_rows):
        conn.execute(
            "INSERT INTO habit_list VALUES (?, ?)", (100 + i, f"payload-{i}")
        )
    conn.commit()
    conn.close()
    return path


def run_restore(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(RESTORE), *args],
        capture_output=True,
        text=True,
        timeout=60,
    )


def habit_rows(db: Path) -> set[str]:
    with sqlite3.connect(db) as conn:
        return {r[0] for r in conn.execute("SELECT data FROM habit_list")}


def test_restore_dry_run_changes_nothing(tmp_path):
    live = make_castor_db(tmp_path / "live.db")
    before = habit_rows(live)
    snapshot = backup.take_snapshot(live, tmp_path / "backups")

    result = run_restore("--snapshot", str(snapshot), "--db", str(live))
    assert result.returncode == 0, result.stderr
    assert "Dry run" in result.stdout
    assert "Nothing was changed" in result.stdout
    assert habit_rows(live) == before


def test_restore_rejects_a_non_castor_snapshot(tmp_path):
    """A snapshot missing the app's tables must never reach a live database."""
    live = make_castor_db(tmp_path / "live.db")
    stranger = tmp_path / "stranger.db"
    conn = sqlite3.connect(stranger)
    conn.execute("CREATE TABLE unrelated (x INTEGER)")
    conn.commit()
    conn.close()

    result = run_restore(
        "--snapshot", str(stranger), "--db", str(live), "--yes"
    )
    assert result.returncode == 1
    assert "missing required table" in result.stderr
    # The live database was left alone.
    assert "user" in {
        r[0]
        for r in sqlite3.connect(live).execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }


def test_restore_rejects_a_corrupt_snapshot(tmp_path):
    live = make_castor_db(tmp_path / "live.db")
    corrupt = tmp_path / "corrupt.db"
    corrupt.write_bytes(b"not a database at all" * 200)

    result = run_restore("--snapshot", str(corrupt), "--db", str(live), "--yes")
    assert result.returncode == 1
    assert "not restorable" in result.stderr


def test_restore_refuses_to_discard_newer_live_data(tmp_path):
    """The safety rail: a snapshot older than live data needs --force."""
    live = make_castor_db(tmp_path / "live.db")
    snapshot = backup.take_snapshot(live, tmp_path / "backups")

    # Live data advances after the snapshot.
    conn = sqlite3.connect(live)
    conn.execute("INSERT INTO habit_list VALUES (999, 'newer-data')")
    conn.commit()
    conn.close()

    # Make the snapshot unambiguously older.
    old = dt.datetime.now() - dt.timedelta(days=1)
    os.utime(snapshot, (old.timestamp(), old.timestamp()))

    result = run_restore("--snapshot", str(snapshot), "--db", str(live), "--yes")
    assert result.returncode == 1
    assert "NEWER than this snapshot" in result.stderr
    assert "newer-data" in habit_rows(live), "live data must be untouched"


def test_restore_brings_back_the_snapshot_state(tmp_path):
    """The full drill: snapshot, lose data, restore, data is back."""
    live = make_castor_db(tmp_path / "live.db")
    original = habit_rows(live)
    snapshot = backup.take_snapshot(live, tmp_path / "backups")

    # Lose data, and add a row that is NOT in the snapshot.
    conn = sqlite3.connect(live)
    conn.execute("DELETE FROM habit_list WHERE id != 1")
    conn.execute("INSERT INTO habit_list VALUES (999, 'post-snapshot')")
    conn.commit()
    conn.close()

    # Snapshot is older than the now-different live file, but this is the
    # intended drill, so --force is the explicit acknowledgement.
    result = run_restore(
        "--snapshot", str(snapshot), "--db", str(live), "--yes", "--force"
    )
    assert result.returncode == 0, result.stderr
    assert habit_rows(live) == original
    assert "post-snapshot" not in habit_rows(live)


def test_restore_keeps_a_reversible_copy_of_the_live_database(tmp_path):
    """A restore must itself be undoable."""
    live = make_castor_db(tmp_path / "live.db")
    snapshot = backup.take_snapshot(live, tmp_path / "backups")

    conn = sqlite3.connect(live)
    conn.execute("INSERT INTO habit_list VALUES (777, 'to-be-lost')")
    conn.commit()
    conn.close()
    live_after_change = habit_rows(live)

    result = run_restore(
        "--snapshot", str(snapshot), "--db", str(live), "--yes", "--force"
    )
    assert result.returncode == 0, result.stderr

    backups = list(tmp_path.glob("live.db.pre-restore-*"))
    assert len(backups) == 1
    # The pre-restore copy still holds the state that was overwritten.
    assert habit_rows(backups[0]) == live_after_change
