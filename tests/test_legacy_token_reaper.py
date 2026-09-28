"""F10: the legacy-token reaper must confirm before it deletes.

The audit's instruction was explicit — "confirm all are invalid, then delete.
Do not delete before confirming invalidation." A cleanup tool that deletes on
assumption is exactly the data-loss pattern F1 is about, applied to a
different directory.

These tests build a synthetic volume with one file of each interesting shape
and assert the verdict, and above all that `--delete` refuses when anything is
unproven.
"""

from __future__ import annotations

import base64
import datetime
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "reap_legacy_nicegui_tokens.py"


def _b64(raw: dict) -> str:
    payload = base64.urlsafe_b64encode(json.dumps(raw).encode()).decode().rstrip("=")
    return payload


def _jwt(ver: int, iat_offset_days: float = -90.0, exp_offset_days: float = 30.0) -> str:
    now = datetime.datetime.now(datetime.timezone.utc).timestamp()
    return (
        _b64({"alg": "HS256", "typ": "JWT"})
        + "."
        + _b64(
            {
                "ver": ver,
                "iat": now + iat_offset_days * 86400,
                "exp": now + exp_offset_days * 86400,
                "sub": "x",
            }
        )
        + ".signature-not-verified-here"
    )


def _volume(tmp_path: Path, users: dict[str, int], files: dict[str, dict]) -> Path:
    volume = tmp_path / "vol"
    (volume / ".nicegui").mkdir(parents=True)
    db = volume / "habits.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE user (id TEXT PRIMARY KEY, token_version INTEGER)")
    conn.executemany("INSERT INTO user VALUES (?, ?)", list(users.items()))
    conn.commit()
    conn.close()
    for name, payload in files.items():
        (volume / ".nicegui" / name).write_text(json.dumps(payload), encoding="utf-8")
    return volume


def _run(volume: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--volume", str(volume), *args],
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_expired_token_is_dead_and_removable(tmp_path):
    volume = _volume(
        tmp_path,
        {"user-a": 0},
        {"storage-user-user-a.json": {"dark_mode": False, "auth_token": _jwt(0, exp_offset_days=-1)}},
    )
    report = _run(volume)
    assert "1 dead, 0 live" in report.stdout

    deleted = _run(volume, "--delete")
    assert deleted.returncode == 0, deleted.stdout + deleted.stderr
    assert not (volume / ".nicegui" / "storage-user-user-a.json").exists()
    # And a backup must exist, so the deletion is reversible.
    backups = list(volume.glob(".nicegui-backup-*"))
    assert backups, "deletion must leave a backup"


def test_token_bumped_by_version_change_is_dead(tmp_path):
    """The cutover bumped token_version, which is what should retire these."""
    volume = _volume(
        tmp_path,
        {"user-b": 3},  # current version is 3
        {"storage-user-user-b.json": {"auth_token": _jwt(1)}},  # token minted at v1
    )
    result = _run(volume, "--delete")
    assert "invalidated by a bump" in result.stdout
    assert result.returncode == 0
    assert not (volume / ".nicegui" / "storage-user-user-b.json").exists()


def test_token_minted_after_the_bump_is_reported_live_and_never_deleted(tmp_path):
    """The audit's stated residual risk: a token written after the bump.

    This is the case that matters. Deleting it silently would remove the only
    record of a credential that still works.
    """
    volume = _volume(
        tmp_path,
        {"user-c": 1},
        {"storage-user-user-c.json": {"auth_token": _jwt(1)}},  # ver == current
    )
    report = _run(volume)
    assert "STILL VALID" in report.stdout
    assert "0 dead, 1 live, 0 unknown" in report.stdout

    deleted = _run(volume, "--delete")
    assert deleted.returncode == 1
    assert "REFUSING TO DELETE" in deleted.stdout
    assert (volume / ".nicegui" / "storage-user-user-c.json").exists()


def test_file_without_a_token_is_safe_to_remove(tmp_path):
    volume = _volume(
        tmp_path,
        {"user-d": 0},
        {"storage-user-user-d.json": {"dark_mode": True}},
    )
    result = _run(volume, "--delete")
    assert result.returncode == 0
    assert "no auth_token present" in result.stdout


def test_unparseable_file_blocks_deletion(tmp_path):
    """A file we cannot understand is a reason to stop, not to delete."""
    volume = _volume(tmp_path, {"user-e": 0}, {})
    (volume / ".nicegui" / "storage-user-user-e.json").write_text("{not json")
    result = _run(volume, "--delete")
    assert result.returncode == 1
    assert "REFUSING TO DELETE" in result.stdout
    assert (volume / ".nicegui" / "storage-user-user-e.json").exists()


def test_token_without_a_ver_claim_is_not_assumed_dead(tmp_path):
    """No 'ver' claim means we cannot prove version-based invalidation."""
    volume = _volume(tmp_path, {"user-f": 5}, {})
    now = datetime.datetime.now(datetime.timezone.utc).timestamp()
    token = _b64({"alg": "HS256"}) + "." + _b64({"iat": now, "exp": now + 99999}) + ".sig"
    (volume / ".nicegui" / "storage-user-user-f.json").write_text(
        json.dumps({"auth_token": token}), encoding="utf-8"
    )
    result = _run(volume, "--delete")
    assert result.returncode == 1
    assert "cannot prove invalidation" in result.stdout
    assert (volume / ".nicegui" / "storage-user-user-f.json").exists()


def test_raw_token_is_never_printed(tmp_path):
    """The tool reports on credentials; it must not leak them into a log."""
    secret = _jwt(0)
    volume = _volume(tmp_path, {"user-g": 0}, {})
    (volume / ".nicegui" / "storage-user-user-g.json").write_text(
        json.dumps({"auth_token": secret}), encoding="utf-8"
    )
    result = _run(volume)
    assert secret not in result.stdout
    assert "sha256:" in result.stdout
    # And the payload segment specifically must not leak.
    assert secret.split(".")[1] not in result.stdout


def test_report_only_by_default_changes_nothing(tmp_path):
    volume = _volume(
        tmp_path,
        {"user-h": 0},
        {"storage-user-user-h.json": {"auth_token": _jwt(0, exp_offset_days=-1)}},
    )
    result = _run(volume)
    assert result.returncode == 0
    assert "Report only" in result.stdout
    assert (volume / ".nicegui" / "storage-user-user-h.json").exists()
    assert not list(volume.glob(".nicegui-backup-*")), "report mode must not back up"
