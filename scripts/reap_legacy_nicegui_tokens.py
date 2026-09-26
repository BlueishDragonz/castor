#!/usr/bin/env python3
"""F10: confirm legacy NiceGUI session files are dead before deleting them.

The audit found 7 `storage-user-<uuid>.json` files under `.user/.nicegui/`,
each holding a raw `auth_token` JWT from the NiceGUI era. The recommendation
was explicit: confirm all are invalid, THEN delete. Do not delete before
confirming.

This tool does the confirming, and refuses to delete anything it has not
proved invalid. It is deliberately read-only by default; `--delete` requires
either that every token was verified dead, or an explicit
--i-have-other-evidence escape that records why.

Safety properties:
  * Never prints a token, only its SHA-256 prefix and its verdict.
  * Reports age and expiry so a "still valid" verdict is checkable by hand.
  * Refuses to delete a file it could not parse or account for. A file with an
    unexpected shape is a reason to stop, not a reason to delete it.
  * Takes a backup of the directory before removing anything.

Usage:
    python scripts/reap_legacy_nicegui_tokens.py --volume /opt/castor-data
    python scripts/reap_legacy_nicegui_tokens.py --volume ... --delete
    python scripts/reap_legacy_nicegui_tokens.py --volume ... --delete \
        --i-have-other-evidence "tokens predate the token_version bump, \
confirmed against cutover log 2026-09-26T02:34:24Z"
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import shutil
import sys
from pathlib import Path

LEGACY_DIRNAME = ".nicegui"


def _fingerprint(token: str) -> str:
    """Stable, non-reversible identifier safe to log."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:12]


def _decode_claims(token: str) -> dict | None:
    """Decode JWT payload without verifying. Never returns the raw token.

    Verification happens against the live database (token_version), not here —
    an unverified decode is only used to report expiry and age.
    """
    import base64
    import json as _json

    parts = token.split(".")
    if len(parts) != 3:
        return None
    payload = parts[1]
    payload += "=" * (-len(payload) % 4)
    try:
        raw = base64.urlsafe_b64decode(payload)
        claims = _json.loads(raw)
    except Exception:
        return None
    return claims if isinstance(claims, dict) else None


def _load_users(db_path: Path) -> dict[str, int]:
    """Map user id -> current token_version from the live database."""
    import sqlite3

    if not db_path.exists():
        raise SystemExit(f"database not found: {db_path}")
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows = conn.execute("SELECT id, token_version FROM user").fetchall()
    finally:
        conn.close()
    return {str(row[0]): int(row[1] or 0) for row in rows}


def audit(directory: Path, db_path: Path) -> tuple[list[dict], list[str]]:
    """Return (findings, problems). Problems block deletion."""
    users = _load_users(db_path)
    findings: list[dict] = []
    problems: list[str] = []

    if not directory.exists():
        return findings, [f"{directory} does not exist"]

    for path in sorted(directory.glob("storage-user-*.json")):
        user_id = path.name.removeprefix("storage-user-").removesuffix(".json")
        record: dict = {
            "file": str(path),
            "user_id": user_id,
            "verdict": "UNKNOWN",
            "reason": "",
            "expired": None,
            "age_days": None,
            "token_fp": None,
            "has_auth_token": False,
        }

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            record["reason"] = f"unparseable: {exc}"
            problems.append(f"{path.name}: {record['reason']}")
            findings.append(record)
            continue

        if not isinstance(data, dict):
            record["reason"] = "unexpected JSON shape"
            problems.append(f"{path.name}: {record['reason']}")
            findings.append(record)
            continue

        token = data.get("auth_token")
        if not isinstance(token, str) or not token:
            # No credential in the file: nothing to leak. Safe.
            record["verdict"] = "DEAD"
            record["reason"] = "no auth_token present"
            findings.append(record)
            continue

        record["has_auth_token"] = True
        record["token_fp"] = _fingerprint(token)
        claims = _decode_claims(token)

        if claims is None:
            record["reason"] = "not a decodable JWT"
            problems.append(f"{path.name}: cannot decode token; not deleting")
            findings.append(record)
            continue

        # Age, for the record.
        iat = claims.get("iat")
        if isinstance(iat, (int, float)):
            minted = datetime.datetime.fromtimestamp(iat, datetime.timezone.utc)
            record["age_days"] = round(
                (datetime.datetime.now(datetime.timezone.utc) - minted).total_seconds()
                / 86400,
                1,
            )

        exp = claims.get("exp")
        if isinstance(exp, (int, float)):
            expires = datetime.datetime.fromtimestamp(exp, datetime.timezone.utc)
            now = datetime.datetime.now(datetime.timezone.utc)
            record["expired"] = expires <= now
            record["expires_at"] = expires.isoformat()

        if record["expired"] is True:
            record["verdict"] = "DEAD"
            record["reason"] = f"expired at {record.get('expires_at')}"
            findings.append(record)
            continue

        # Not expired by time — the only thing that can retire it is a
        # token_version bump. This is the check the audit asked for.
        token_ver = claims.get("ver")
        if user_id not in users:
            record["reason"] = "no such user in the database"
            record["verdict"] = "DEAD"
            findings.append(record)
            continue

        current = users[user_id]
        record["current_token_version"] = current

        if not isinstance(token_ver, int):
            record["reason"] = "token carries no integer 'ver' claim"
            record["verdict"] = "UNKNOWN"
            problems.append(
                f"{path.name}: token has no integer 'ver' claim; "
                "cannot prove invalidation by version"
            )
            findings.append(record)
            continue

        if token_ver < current:
            record["verdict"] = "DEAD"
            record["reason"] = (
                f"token_version {token_ver} < current {current} "
                "(invalidated by a bump)"
            )
        else:
            record["verdict"] = "LIVE"
            record["reason"] = (
                f"token_version {token_ver} >= current {current}; "
                "this token is still a valid credential"
            )
            problems.append(
                f"{path.name}: STILL VALID — do not delete without revoking"
            )
        findings.append(record)

    return findings, problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--volume", required=True, help="path to the data volume")
    parser.add_argument(
        "--db", default=None, help="path to habits.db (default: <volume>/habits.db)"
    )
    parser.add_argument(
        "--delete",
        action="store_true",
        help="actually remove verified-dead files (default is report only)",
    )
    parser.add_argument(
        "--i-have-other-evidence",
        metavar="REASON",
        default=None,
        help="override the blocker and record why (recorded in the output)",
    )
    args = parser.parse_args()

    volume = Path(args.volume).resolve()
    db_path = Path(args.db) if args.db else volume / "habits.db"
    directory = volume / LEGACY_DIRNAME

    findings, problems = audit(directory, db_path)

    print(f"Legacy NiceGUI session files: {directory}")
    print(f"Database: {db_path}")
    print()
    for record in findings:
        print(f"  {Path(record['file']).name}")
        print(f"    verdict : {record['verdict']}")
        print(f"    reason  : {record['reason']}")
        if record["token_fp"]:
            print(f"    token   : sha256:{record['token_fp']} (not printed)")
        if record["age_days"] is not None:
            print(f"    age     : {record['age_days']} days")
        if record.get("current_token_version") is not None:
            print(f"    current : token_version={record['current_token_version']}")
        print()

    live = [f for f in findings if f["verdict"] == "LIVE"]
    dead = [f for f in findings if f["verdict"] == "DEAD"]
    unknown = [f for f in findings if f["verdict"] == "UNKNOWN"]

    print(
        f"Summary: {len(dead)} dead, {len(live)} live, "
        f"{len(unknown)} unknown, {len(findings)} total"
    )

    # The blockers are as important as the per-file verdicts: they are the
    # reason deletion is being refused, and an operator who cannot see them
    # has no way to know what to fix.
    if problems:
        print("\nBlocking problems:")
        for problem in problems:
            print(f"  - {problem}")

    if args.i_have_other_evidence:
        print(f"\nOVERRIDE: {args.i_have_other_evidence}")

    if not args.delete:
        print("\nReport only (pass --delete to remove verified-dead files).")
        return 1 if (live or unknown) else 0

    if (live or unknown) and not args.i_have_other_evidence:
        print(
            "\nREFUSING TO DELETE: some files are live or unproven.\n"
            "Revoke the live tokens first, or pass --i-have-other-evidence "
            "with a reason."
        )
        return 1

    # Only remove files individually verified DEAD.
    victims = [Path(f["file"]) for f in findings if f["verdict"] == "DEAD"]
    if not victims:
        print("\nNothing verified dead; nothing deleted.")
        return 1

    backup = directory.parent / f".nicegui-backup-{datetime.datetime.now():%Y%m%dT%H%M%S}"
    shutil.copytree(directory, backup)
    print(f"\nBacked up {directory} -> {backup}")

    for path in victims:
        path.unlink()
        print(f"  removed {path.name}")

    remaining = list(directory.glob("storage-user-*.json"))
    if remaining:
        print(f"\n{len(remaining)} file(s) left in place (not verified dead):")
        for path in remaining:
            print(f"  {path.name}")
    else:
        print("\nAll verified-dead files removed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
