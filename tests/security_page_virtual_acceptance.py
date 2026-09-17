"""Full, unmodified NiceGUI Security page acceptance with real WebAuthn.

Run in a fresh process: .venv/bin/python tests/security_page_virtual_acceptance.py
Only disposable local state is used; no mocks, auth overrides, or deployment.
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import secrets
import socket
import sqlite3
import sys
import tempfile
import uuid

ROOT = Path(__file__).resolve().parents[1]
EMAIL = "page-acceptance@example.invalid"
NAME = "Page acceptance 🔑"


async def acceptance(folder: Path, measurements: dict) -> None:
    os.chdir(folder)
    # The full app resolves these read-only assets relative to cwd. All mutable
    # .user / .nicegui files remain inside the disposable directory.
    (folder / "statics").symlink_to(ROOT / "statics", target_is_directory=True)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    origin = f"http://localhost:{sock.getsockname()[1]}"
    database = folder / "acceptance.db"
    os.environ.update({
        "PYTHON_DOTENV_DISABLED": "1",
        "DATABASE_URL": f"sqlite+aiosqlite:///{database}",
        "JWT_SECRET": secrets.token_urlsafe(48),
        "RESET_PASSWORD_TOKEN_SECRET": secrets.token_urlsafe(48),
        "NICEGUI_STORAGE_SECRET": secrets.token_urlsafe(48),
        "JWT_LIFETIME_SECONDS": "600",
        "APP_URL": origin, "WEBAUTHN_ORIGIN": origin,
        "WEBAUTHN_RP_ID": "localhost", "CSRF_ALLOWED_ORIGINS": json.dumps([origin]),
        "TRUSTED_EMAIL_HEADER": "", "TRUSTED_LOCAL_EMAIL": "",
        "SENTRY_DSN": "", "ENV": "dev", "DEBUG": "false",
        "ENABLE_DAILY_BACKUP": "false", "ENABLE_PLAN": "false",
        "REQUIRE_ADMIN_FOR_REGISTRATION": "false",
        "GOOGLE_ONE_TAP_ENABLED": "false", "UMAMI_ANALYTICS_ID": "",
        "HIGHLIGHT_KEY": "", "HIGHLIGHT_IO_PROJECT_ID": "",
        "NICEGUI_STORAGE_PATH": str(folder / ".nicegui"),
    })
    sys.path.insert(0, str(ROOT))
    server = task = engine = None
    measurements["stage"] = "import full application"
    try:
        import httpx
        import uvicorn
        from playwright.async_api import async_playwright, expect
        from fastapi_users.password import PasswordHelper
        from beaverhabits.main import app
        from beaverhabits.app import db
        from beaverhabits.configs import settings

        engine = db.engine
        assert settings.DATABASE_URL == os.environ["DATABASE_URL"]
        assert settings.WEBAUTHN_ORIGIN == origin
        assert settings.WEBAUTHN_RP_ID == "localhost"
        assert not settings.TRUSTED_EMAIL_HEADER and not settings.TRUSTED_LOCAL_EMAIL
        assert not app.dependency_overrides
        server = uvicorn.Server(uvicorn.Config(app, log_level="error", access_log=False))
        task = asyncio.create_task(server.serve(sockets=[sock]))
        measurements["stage"] = "full application readiness"
        async with httpx.AsyncClient(base_url=origin, trust_env=False) as client:
            for _ in range(200):
                if task.done():
                    await task
                    raise RuntimeError("full application stopped before readiness")
                try:
                    if (await client.get("/health", timeout=0.5)).status_code == 200:
                        break
                except httpx.TransportError:
                    pass
                await asyncio.sleep(0.05)
            else:
                raise RuntimeError("full application /health did not become ready")

            password = secrets.token_urlsafe(40)
            owner = uuid.uuid4()
            async with db.async_session_maker() as session:
                session.add(db.User(id=owner, email=EMAIL, is_active=True,
                                    is_verified=True, is_superuser=False, token_version=0,
                                    hashed_password=PasswordHelper().hash(password)))
                await session.commit()
            measurements["stage"] = "real POST /auth/login"
            login = await client.post("/auth/login", data={"username": EMAIL, "password": password})
            measurements["login_http"] = login.status_code
            assert login.status_code == 200, f"/auth/login returned {login.status_code}"
            token = login.json()["access_token"]

            with sqlite3.connect(database) as connection:
                assert connection.execute("SELECT count(*) FROM webauthn_credential").fetchone()[0] == 0
                assert connection.execute("SELECT count(*) FROM webauthn_challenge").fetchone()[0] == 0

            async with async_playwright() as playwright:
                browser = await playwright.chromium.launch(headless=True)
                try:
                    measurements["chromium"] = browser.version
                    context = await browser.new_context()
                    await context.add_cookies([{
                        "name": "beaver_auth", "value": token, "domain": "localhost",
                        "path": "/", "httpOnly": True, "secure": False, "sameSite": "Lax",
                    }])
                    page = await context.new_page()
                    page.set_default_timeout(20000)
                    errors = []
                    # Keep only error types, never request bodies/cookies or tokens.
                    page.on("pageerror", lambda error: errors.append(type(error).__name__))
                    statuses = []
                    page.on("response", lambda response: statuses.append(
                        (response.url.removeprefix(origin), response.status))
                        if response.url.startswith(origin + "/auth/webauthn/register/") else None)
                    cdp = await context.new_cdp_session(page)
                    await cdp.send("WebAuthn.enable")
                    await cdp.send("WebAuthn.addVirtualAuthenticator", {"options": {
                        "protocol": "ctap2", "transport": "internal",
                        "hasResidentKey": True, "hasUserVerification": True,
                        "isUserVerified": True, "automaticPresenceSimulation": True,
                    }})
                    measurements["stage"] = "open /gui/security with login cookie"
                    response = await page.goto(origin + "/gui/security")
                    measurements["security_http"] = response.status if response else None
                    measurements["security_path"] = page.url.removeprefix(origin).split("?")[0]
                    assert response and response.status == 200, "Security page did not return HTTP 200"
                    assert page.url == origin + "/gui/security", "Security page redirected instead of accepting login cookie"
                    assert await page.evaluate("window.isSecureContext")
                    await expect(page.get_by_text("No passkeys yet", exact=True)).to_be_visible()
                    await expect(page.get_by_text(EMAIL, exact=True)).to_be_visible()
                    measurements["stage"] = "open Add passkey dialog"
                    await page.get_by_role("button", name="Add passkey", exact=True).click()
                    nickname = page.get_by_label("Passkey nickname", exact=True)
                    await expect(nickname).to_have_value("")
                    await nickname.fill(NAME)
                    measurements["stage"] = "submit real registration and await success stage"
                    await page.get_by_role("button", name="Register passkey", exact=True).click()
                    await expect(page.get_by_role("heading", name="Passkey added", exact=True)).to_be_visible(timeout=30000)
                    measurements["success_stage"] = True
                    await page.get_by_role("button", name="Done", exact=True).click()
                    measurements["stage"] = "verify live list re-render (without reload)"
                    await expect(page.locator(".bh-security").get_by_text(NAME, exact=True)).to_be_visible()
                    await expect(page.get_by_text("No passkeys yet", exact=True)).to_have_count(0)
                    measurements["list_rerender_name"] = NAME
                    assert statuses == [("/auth/webauthn/register/begin", 200),
                                        ("/auth/webauthn/register/complete", 200)], "Unexpected ceremony HTTP statuses"
                    measurements["registration_http"] = [status for _, status in statuses]
                    measurements["page_errors"] = len(errors)
                    assert not errors, "Browser page errors occurred"
                    storage = await page.evaluate("({local: localStorage.length, session: sessionStorage.length})")
                    measurements["web_storage"] = storage
                    # Report framework keys only, never storage values. Do not
                    # clear/ignore these keys to manufacture an empty-storage pass.
                    measurements["nicegui_session_keys"] = await page.evaluate(
                        "Object.keys(sessionStorage).filter(k => k === '__nicegui_tab_id' || k === '__nicegui_tab_closed')"
                    )
                    cookies = await context.cookies()
                    assert any(c["name"] == "beaver_auth" and c["httpOnly"] and c["path"] == "/" for c in cookies)
                    measurements['stage'] = 'full-page password change and revoked session'
                    await page.get_by_role('button', name='Change password', exact=True).click()
                    replacement = secrets.token_urlsafe(40)
                    await page.get_by_label('Current password', exact=True).fill(password)
                    await page.get_by_label('New password', exact=True).fill(replacement)
                    await page.get_by_label('Confirm new password', exact=True).fill(replacement)
                    await page.get_by_role('button', name='Save password', exact=True).click()
                    await expect(page.get_by_role('heading', name='Password changed', exact=True)).to_be_visible()
                    assert await page.locator('input:visible').count() == 0
                    revoked = await client.get('/auth/webauthn/credentials', headers={'Authorization': 'Bearer ' + token})
                    assert revoked.status_code == 401, 'Old session was not revoked'
                    fresh = await client.post('/auth/login', data={'username': EMAIL, 'password': replacement})
                    assert fresh.status_code == 200, 'New password login failed'
                    password = replacement = ''
                    await page.get_by_role('button', name='Sign in', exact=True).click()
                    await page.wait_for_url(origin + '/login')
                    measurements['password_change_success'] = True
                    measurements['old_session_http'] = revoked.status_code
                    measurements['new_password_login_http'] = fresh.status_code
                    assert not errors, 'Browser errors during password change'
                    measurements['page_errors'] = len(errors)
                finally:
                    await browser.close()

            measurements["stage"] = "independent committed SQLite verification"
            with sqlite3.connect(database) as connection:
                rows = connection.execute(
                    "SELECT name, user_id, length(public_key) FROM webauthn_credential"
                ).fetchall()
                assert len(rows) == 1, "Expected exactly one committed passkey"
                assert rows[0][0] == NAME, "Committed nickname mismatch"
                assert uuid.UUID(str(rows[0][1])) == owner, "Committed user mismatch"
                assert rows[0][2] > 0, "Missing public key"
                challenges = connection.execute("SELECT count(*) FROM webauthn_challenge").fetchone()[0]
                assert challenges == 0, "Registration challenge not consumed"
            measurements.update(persisted_credentials=len(rows), name_roundtrip=True,
                                user_match=True, public_key_bytes=rows[0][2],
                                remaining_challenges=challenges, real_verifier=True,
                                full_app=True, auth_dependency_overrides=False)
            measurements["stage"] = "assert no credential storage"
            assert storage["local"] == 0, "Unexpected local storage"
            assert storage["session"] == len(measurements["nicegui_session_keys"]), "Unexpected session storage"
            measurements["credential_storage_empty"] = True
            measurements.update(stage="complete", status="PASS")
    finally:
        if server is not None:
            server.should_exit = True
        if task is not None:
            try:
                await asyncio.wait_for(task, timeout=20)
            except asyncio.TimeoutError:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        sock.close()
        if engine is not None:
            await engine.dispose()


def main() -> int:
    result = {"status": "FAIL", "temporary_database": True}
    cwd = Path.cwd()
    try:
        with tempfile.TemporaryDirectory(prefix="security-page-acceptance-") as temp:
            try:
                asyncio.run(acceptance(Path(temp), result))
            except Exception as error:
                # Playwright errors can embed input arguments; report the failing
                # stage and error type, not arbitrary exception text or secrets.
                result["error_type"] = type(error).__name__
            finally:
                os.chdir(cwd)
        result["temporary_database_removed"] = not Path(temp).exists()
    finally:
        os.chdir(cwd)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
