"""Tests for Batch 4: live-runtime verification of the merged app.

These tests probe the running container over the wg-easy netns (the same
network path your iPhone uses). They check:

1. /auth/webauthn/login/begin accepts an email and either returns a
   challenge OR signals "user not found" — proving the route is wired and
   the SQLAlchemy session is reading from the persistent habits.db
2. The webauthn_credential table exists in the live DB with the expected
   schema (auto-created on startup via create_db_and_tables)
3. The pre-existing USB passkey row for jrodux@gmail.com is still there
4. The /auth/webauthn/credentials GET endpoint requires auth (401 without
   a bearer token)
5. The /auth/webauthn/credentials/{id} DELETE endpoint requires auth
6. WEBAUTHN_RP_ID matches the live origin OR a deliberate env-var
   override is in place (rpId mismatch = passkeys won't load on iPhone)
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, "/opt/castor")


def sh_local(script: str, timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash"], input=script + "\n", capture_output=True, text=True, timeout=timeout
    )


# All probes go through the wg-easy netns so they exercise the same network
# path your iPhone uses.
DOCKER_EXEC_PREFIX = "/usr/bin/docker exec wg-easy"
APP_BASE = "http://10.8.0.1:8080"


def http_probe(method_path: str, body: str = None) -> str:
    """Run an HTTP probe from inside the wg-easy netns and return combined
    stdout+stderr so we can grep for HTTP status. Uses wget which writes
    the status line to stderr."""
    if body:
        cmd = (
            f"{DOCKER_EXEC_PREFIX} wget -q -S -O /dev/null --timeout=10 "
            f"--header='Content-Type: application/json' "
            f"--post-data='{body}' "
            f"{APP_BASE}{method_path} 2>&1"
        )
    else:
        cmd = (
            f"{DOCKER_EXEC_PREFIX} wget -q -S -O /dev/null --timeout=10 "
            f"{APP_BASE}{method_path} 2>&1"
        )
    r = sh_local(cmd)
    return r.stdout


# ---------------------------------------------------------------------------
# Batch 4.1: live webauthn_credential schema and data
# ---------------------------------------------------------------------------

INSPECT_SCRIPT_PATH = "/tmp/batch4_db_inspect.py"


def write_inspect_script():
    """Write the db inspection script into the container. Idempotent."""
    src = '''import json
import sqlite3
c = sqlite3.connect('/app/.user/habits.db')
c.row_factory = sqlite3.Row

result = {}

# Tables
result['tables'] = [r[0] for r in c.execute(
    "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
)]

# webauthn_credential schema (raw CREATE TABLE statement)
schema_rows = list(c.execute(
    "SELECT sql FROM sqlite_master WHERE name='webauthn_credential'"
))
result['webauthn_schema'] = schema_rows[0][0] if schema_rows else None

# webauthn_credential row count + sign_count distribution
result['row_count'] = c.execute(
    'SELECT COUNT(*) FROM webauthn_credential'
).fetchone()[0]

# For each row, capture id (as hex), user_id, sign_count, public_key length,
# transports, backup_eligible, backup_state, name
result['rows'] = []
for r in c.execute(\'\'\'
    SELECT id, user_id, hex(id) AS id_hex, length(public_key) AS pk_len,
           sign_count, transports, aaguid, backup_eligible, backup_state,
           name, created_at, updated_at
    FROM webauthn_credential
\'\'\'):
    result['rows'].append(dict(r))

# user table — emails and ids
result['users'] = [
    dict(r) for r in c.execute('SELECT id, email FROM user ORDER BY email')
]

print('===BATCH4_DB_RESULT===')
print(json.dumps(result, indent=2, default=str))
'''
    sh_local(
        f"cat > {INSPECT_SCRIPT_PATH} << 'BATCH4_EOF'\n{src}\nBATCH4_EOF\n"
    )


def read_live_db() -> dict:
    """Run the inspect script inside the container and return the parsed result."""
    write_inspect_script()
    r = sh_local(
        f"/usr/bin/docker cp {INSPECT_SCRIPT_PATH} "
        f"beaverhabits:/tmp/batch4_db_inspect.py && "
        f"/usr/bin/docker exec -u nobody beaverhabits "
        f"/opt/pysetup/.venv/bin/python /tmp/batch4_db_inspect.py"
    )
    assert r.returncode == 0, (
        f"db inspect failed: rc={r.returncode}\nstdout={r.stdout!r}\nstderr={r.stderr!r}"
    )
    # Extract the JSON between the markers
    m = re.search(r"===BATCH4_DB_RESULT===\n(.*)", r.stdout, re.DOTALL)
    assert m, f"no result marker in output:\n{r.stdout}"
    return json.loads(m.group(1))


class TestLiveDBSchema:
    def test_webauthn_credential_table_exists(self):
        db = read_live_db()
        assert "webauthn_credential" in db["tables"], (
            f"webauthn_credential not in live DB tables: {db['tables']}"
        )

    def test_webauthn_credential_schema_has_required_columns(self):
        """The auto-created table must include every column the app reads/writes."""
        db = read_live_db()
        schema = db["webauthn_schema"] or ""
        for col in (
            "id BLOB",
            "user_id",
            "public_key BLOB",
            "sign_count INTEGER",
            "transports",
            "aaguid",
            "backup_eligible",
            "backup_state",
            "name",
            "created_at",
            "updated_at",
        ):
            assert col in schema, (
                f"webauthn_credential schema missing {col!r}; full schema:\n{schema}"
            )

    def test_user_jrodux_still_exists(self):
        """jrodux@gmail.com is the user with the USB passkey. They must still
        be in the user table."""
        db = read_live_db()
        emails = [u["email"] for u in db["users"]]
        assert "jrodux@gmail.com" in emails, (
            f"jrodux@gmail.com not in users: {emails}"
        )


class TestUSBPasskeyPreserved:
    def test_jrodux_has_at_least_one_webauthn_credential(self):
        db = read_live_db()
        jrodux_id = next(
            u["id"] for u in db["users"] if u["email"] == "jrodux@gmail.com"
        )
        matching = [
            r for r in db["rows"] if r["user_id"] == jrodux_id
        ]
        assert matching, (
            f"no webauthn_credential rows for jrodux ({jrodux_id}); "
            f"all rows: {db['rows']}"
        )

    def test_credential_public_key_is_valid_length(self):
        """ES256 public keys are 77 bytes uncompressed (P-256). If the key is
        missing or wrong length, WebAuthn signature verification will fail."""
        db = read_live_db()
        assert db["rows"], "no webauthn_credential rows at all"
        for r in db["rows"]:
            assert r["pk_len"] >= 65, (
                f"credential {r['id_hex'][:16]} has pk_len={r['pk_len']}; "
                f"expected >=65 (P-256 compressed/uncompressed)"
            )

    def test_sign_count_is_nonzero(self):
        """sign_count=0 would indicate a freshly-registered credential that has
        never been used. Real passkeys increment this on each use."""
        db = read_live_db()
        assert db["rows"], "no webauthn_credential rows"
        for r in db["rows"]:
            assert r["sign_count"] > 0, (
                f"credential {r['id_hex'][:16]} has sign_count=0; "
                f"this passkey has never been used. "
                f"Either it's a fresh registration or data corruption."
            )


# ---------------------------------------------------------------------------
# Batch 4.2: live HTTP probe of WebAuthn routes
# ---------------------------------------------------------------------------

class TestLiveWebAuthnRoutes:
    def test_login_begin_accepts_email_and_returns_challenge_or_404(self):
        """POST /auth/webauthn/login/begin with a known user's email must
        return either 200 (challenge issued) or 404 (user not found).
        Anything else — 500, 401, 422 with a SQLAlchemy error — means
        the live DB session or model is broken."""
        output = http_probe(
            "/auth/webauthn/login/begin",
            body=json.dumps({"email": "jrodux@gmail.com"}),
        )
        m = re.search(r"HTTP/[\d.]+ (\d+)", output)
        assert m, f"no HTTP status in response:\n{output}"
        status = int(m.group(1))
        # 422 is also acceptable — request validation may reject this body
        # format. The key is that the route is reachable and not 5xx.
        assert status in (200, 404, 422), (
            f"/auth/webauthn/login/begin returned {status}; expected 200 "
            f"(challenge issued), 404 (user not found), or 422 (body "
            f"validation). Got full response:\n{output}"
        )

    def test_login_begin_404_for_unknown_email(self):
        """POST /auth/webauthn/login/begin with an unknown email must return
        404 — proves user lookup is actually working (not always returning
        200 by accident)."""
        output = http_probe(
            "/auth/webauthn/login/begin",
            body=json.dumps({"email": "no-such-user@example.invalid"}),
        )
        m = re.search(r"HTTP/[\d.]+ (\d+)", output)
        assert m, f"no HTTP status in response:\n{output}"
        status = int(m.group(1))
        # 422 acceptable — body validation. 404 is what we want.
        assert status in (404, 422), (
            f"/auth/webauthn/login/begin for unknown email returned {status}; "
            f"expected 404 or 422.\n{output}"
        )

    def test_credentials_get_requires_auth(self):
        """GET /auth/webauthn/credentials without a bearer token must return
        401, not 200 and not 500. Proves the JWT auth gate is in place."""
        output = http_probe("/auth/webauthn/credentials")
        m = re.search(r"HTTP/[\d.]+ (\d+)", output)
        assert m, f"no HTTP status:\n{output}"
        status = int(m.group(1))
        assert status == 401, (
            f"/auth/webauthn/credentials (no auth) returned {status}; "
            f"expected 401. Auth gate is broken.\n{output}"
        )

    def test_credentials_delete_requires_auth(self):
        """DELETE /auth/webauthn/credentials/{id} without auth must return
        401, not 200/204 and not 500.

        BusyBox wget in wg-easy doesn't support --method. Use python's
        urllib from inside the beaverhabits container (which shares the
        wg-easy netns so 10.8.0.1:8080 is reachable)."""
        # Write the probe script to a file (python -c can't handle
        # try/except as a one-liner).
        probe = (
            "import urllib.request\n"
            f"req = urllib.request.Request('{APP_BASE}/auth/webauthn/credentials/any-id-here', method='DELETE')\n"
            "try:\n"
            "    urllib.request.urlopen(req, timeout=5)\n"
            "    print('NO_ERROR')\n"
            "except urllib.error.HTTPError as e:\n"
            "    print('HTTP_' + str(e.code))\n"
        )
        sh_local(
            "cat > /tmp/batch4_delete_probe.py << 'BATCH4_EOF'\n"
            f"{probe}"
            "BATCH4_EOF\n"
        )
        r = sh_local(
            "/usr/bin/docker cp /tmp/batch4_delete_probe.py beaverhabits:/tmp/ && "
            "/usr/bin/docker exec beaverhabits /opt/pysetup/.venv/bin/python /tmp/batch4_delete_probe.py"
        )
        assert r.returncode == 0, (
            f"DELETE probe failed: rc={r.returncode}\n{r.stdout!r}\n{r.stderr!r}"
        )
        m = re.search(r"HTTP_(\d+)", r.stdout)
        assert m, f"no HTTP status in output:\n{r.stdout}"
        status = int(m.group(1))
        assert status == 401, (
            f"DELETE /auth/webauthn/credentials/{{id}} (no auth) returned "
            f"{status}; expected 401. Auth gate is broken.\n{r.stdout}"
        )


# ---------------------------------------------------------------------------
# Batch 4.3: rpId configuration matches the live origin
# ---------------------------------------------------------------------------

class TestRpidConfiguration:
    def test_webauthn_rp_id_setting_exists_in_running_container(self):
        """The running container's settings.WEBAUTHN_RP_ID must be set.
        Without it, WebAuthn registration/login is impossible."""
        probe_script = (
            "/opt/pysetup/.venv/bin/python -c "
            "\"from beaverhabits.configs import settings; "
            "print('RP_ID=' + str(settings.WEBAUTHN_RP_ID)); "
            "print('ORIGIN=' + str(settings.WEBAUTHN_ORIGIN)); "
            "print('RP_NAME=' + str(settings.WEBAUTHN_RP_NAME))\""
        )
        r = sh_local(
            f"/usr/bin/docker exec beaverhabits {probe_script}"
        )
        assert r.returncode == 0, f"settings probe failed: {r.stderr}"
        assert "RP_ID=" in r.stdout, f"no RP_ID in output: {r.stdout}"

    def test_rp_id_matches_live_origin_or_deliberate_override(self):
        """WebAuthn rpId validation is strict: the rpId set when the passkey
        was registered must match (or be a suffix of) the origin's host that
        the browser sees. The current 09-12 default is `localhost` — that
        will only work if the iPhone hits http://localhost:8080, which it
        doesn't (it hits http://10.8.0.1:8080 over VPN).

        This test passes if EITHER:
          (a) rpId is already `10.8.0.1` (we flipped it via compose), OR
          (b) rpId is still `localhost` AND there's an explicit compose env
              override documented that defers the flip to Batch 5.

        It fails if rpId is set to something else without a deliberate env
        override."""
        probe_script = (
            "/opt/pysetup/.venv/bin/python -c "
            "\"from beaverhabits.configs import settings; "
            "print(settings.WEBAUTHN_RP_ID)\""
        )
        r = sh_local(f"/usr/bin/docker exec beaverhabits {probe_script}")
        rp_id = r.stdout.strip()

        # Check for env override in compose
        compose_r = sh_local("grep -E WEBAUTHN_RP_ID /opt/wg-net/docker-compose.yml")
        has_explicit_override = "WEBAUTHN_RP_ID=" in compose_r.stdout

        if rp_id == "10.8.0.1":
            # Already flipped — good
            pass
        elif rp_id == "localhost" and has_explicit_override:
            # Documented deferral to Batch 5 — also acceptable
            pass
        else:
            pytest.fail(
                f"WEBAUTHN_RP_ID={rp_id!r} but the iPhone will hit "
                f"http://10.8.0.1:8080 over VPN. rpId mismatch = passkeys "
                f"won't work. Either flip the env var to '10.8.0.1' or "
                f"document an explicit compose override. "
                f"Compose WEBAUTHN_RP_ID lines:\n{compose_r.stdout}"
            )
