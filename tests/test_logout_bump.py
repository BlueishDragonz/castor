"""Integration tests for the /auth/logout endpoint.

The endpoint is mounted by castor.app.webauthn_routes.logout_router
after fastapi-users' auth router so it shadows the default logout
behaviour. It MUST bump user.token_version server-side so that any
JWT issued before logout is rejected by VersionedJWTStrategy on the
next request — that's the D14 / stolen-JWT-after-logout story.

Background: dogfood on 2026-09-23 discovered that
user_bump_token_version was calling session.refresh(user) against
a user loaded by a different (dependency-injection) session. The
refresh raised InvalidRequestError and rolled back the UPDATE, so
the DB column stayed at 0 even though the endpoint returned 204.
There was no test that would have caught this — these tests close
that gap.
"""
import os
import secrets
import tempfile

import pytest
from fastapi.testclient import TestClient

# Set env vars BEFORE importing the app (settings load at import time).
_BOOT = tempfile.TemporaryDirectory()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_BOOT.name}/logout.db"
os.environ["JWT_SECRET"] = secrets.token_urlsafe(32)
os.environ["RESET_PASSWORD_TOKEN_SECRET"] = secrets.token_urlsafe(32)
os.environ["NICEGUI_STORAGE_SECRET"] = secrets.token_urlsafe(32)
os.environ["TRUSTED_LOCAL_EMAIL"] = ""
os.environ["TRUSTED_EMAIL_HEADER"] = ""
os.environ["SENTRY_DSN"] = ""

from castor.main import app  # noqa: E402  (must follow env setup)

PASSWORD = "TestPassword1234!"


@pytest.fixture(name="client", scope="module")
def client_fixture():
    with TestClient(app, raise_server_exceptions=True) as client:
        yield client


def _register_and_login(client: TestClient) -> str:
    """Register a fresh user, log them in, return the bearer access_token."""
    email = f"logout_test_{secrets.token_urlsafe(8)}@example.com"
    resp = client.post(
        "/auth/register",
        json={"email": email, "password": PASSWORD},
        headers={"Origin": "http://localhost:4321"},
    )
    assert resp.status_code == 201, resp.text

    resp = client.post(
        "/auth/login",
        data={"grant_type": "password", "username": email, "password": PASSWORD},
        headers={"Content-Type": "application/x-www-form-urlencoded", "Origin": "http://localhost:4321"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


def test_logout_returns_204_and_bumps_token_version(client: TestClient):
    """The endpoint must return 204 AND persist a token_version bump."""
    token = _register_and_login(client)

    # Sanity: the token works before logout.
    whoami = client.get(
        "/users/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert whoami.status_code == 200, whoami.text

    # POST /auth/logout — should bump the DB column and return 204.
    resp = client.post(
        "/auth/logout",
        headers={"Authorization": f"Bearer {token}", "Origin": "http://localhost:4321"},
    )
    assert resp.status_code == 204, resp.text

    # The OLD token must now be rejected by VersionedJWTStrategy.
    # /users/me reads the JWT via fastapi-users' current_active_user
    # which goes through read_token -> user.token_version check.
    whoami_after = client.get(
        "/users/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert whoami_after.status_code == 401, (
        f"expected 401 after logout, got {whoami_after.status_code}: "
        f"{whoami_after.text}. The JWT's 'ver' claim must NOT match "
        f"the user's token_version after /auth/logout returns 204."
    )


def test_logout_only_invalidates_the_logged_out_user(client: TestClient):
    """One user's logout must not bump token_version for anyone else."""
    # User A: log in, log out.
    token_a = _register_and_login(client)
    resp = client.post(
        "/auth/logout",
        headers={"Authorization": f"Bearer {token_a}", "Origin": "http://localhost:4321"},
    )
    assert resp.status_code == 204

    # User B: log in fresh. Their JWT must still work.
    token_b = _register_and_login(client)
    whoami_b = client.get(
        "/users/me",
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert whoami_b.status_code == 200, (
        f"user B's JWT should still be valid after user A's logout; "
        f"got {whoami_b.status_code}: {whoami_b.text}"
    )
