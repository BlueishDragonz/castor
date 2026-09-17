"""Isolated real-WebAuthn happy-path acceptance; run directly with .venv/bin/python.

No production modules are modified, no authentication dependencies/verifiers are
mocked, and no live service or production data is accessed. The minimal HTML
shell loads the real security_passkeys.js; it is not the full NiceGUI page.
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
EMAIL = "virtual-acceptance@example.invalid"
NAME = "Acceptance backup 🔑"


async def acceptance(folder: Path) -> dict:
    # Configure BEFORE importing any application module. A fresh subprocess is
    # required: this script intentionally does not run in a shared pytest worker.
    os.chdir(folder)
    os.environ.update({
        "PYTHON_DOTENV_DISABLED": "1",
        "DATABASE_URL": f"sqlite+aiosqlite:///{folder / 'acceptance.db'}",
        "JWT_SECRET": secrets.token_urlsafe(48),
        "JWT_LIFETIME_SECONDS": "600",
        "TRUSTED_EMAIL_HEADER": "", "TRUSTED_LOCAL_EMAIL": "",
        "SENTRY_DSN": "", "ENV": "dev",
    })
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    origin = f"http://localhost:{sock.getsockname()[1]}"
    os.environ.update({"APP_URL": origin, "WEBAUTHN_ORIGIN": origin,
                       "WEBAUTHN_RP_ID": "localhost"})
    sys.path.insert(0, str(ROOT))

    from fastapi import FastAPI
    from fastapi.responses import FileResponse, HTMLResponse
    import httpx
    from playwright.async_api import async_playwright
    import uvicorn
    from beaverhabits.app import db, webauthn_routes
    from beaverhabits.app.users import get_jwt_strategy
    from fastapi_users.password import PasswordHelper
    from beaverhabits.configs import settings

    assert settings.DATABASE_URL == os.environ["DATABASE_URL"]
    assert settings.WEBAUTHN_ORIGIN == origin
    assert not settings.TRUSTED_EMAIL_HEADER and not settings.TRUSTED_LOCAL_EMAIL
    async with db.engine.begin() as connection:
        await connection.run_sync(db.Base.metadata.create_all)
    async with db.async_session_maker() as session:
        user = db.User(id=uuid.uuid4(), email=EMAIL, is_active=True,
                       is_verified=True, is_superuser=False, token_version=0,
                       hashed_password=PasswordHelper().hash(secrets.token_urlsafe(40)))
        session.add(user)
        await session.commit()
        token = await get_jwt_strategy().write_token(user)
        owner = user.id.hex

    app = FastAPI()
    app.include_router(webauthn_routes.router)

    @app.get("/")
    async def shell():
        return HTMLResponse('<!doctype html><title>Isolated WebAuthn acceptance</title>'
                            '<script src="/security_passkeys.js"></script>')

    @app.get("/security_passkeys.js")
    async def script():
        return FileResponse(ROOT / "beaverhabits/frontend/security_passkeys.js",
                            media_type="application/javascript")

    server = uvicorn.Server(uvicorn.Config(app, log_level="error", access_log=False))
    task = asyncio.create_task(server.serve(sockets=[sock]))
    browser = None
    try:
        async with httpx.AsyncClient(base_url=origin) as client:
            # Bounded readiness check, not a blind startup delay.
            for _ in range(100):
                if task.done():
                    await task
                    raise RuntimeError("server stopped before readiness")
                try:
                    if (await client.get("/", timeout=0.5)).status_code == 200:
                        break
                except httpx.TransportError:
                    pass
                await asyncio.sleep(0.05)
            else:
                raise RuntimeError("loopback server did not become ready")
            headers = {"Authorization": f"Bearer {token}"}
            before = await client.get("/auth/webauthn/credentials", headers=headers)
            assert before.status_code == 200 and before.json() == []

            async with async_playwright() as playwright:
                browser = await playwright.chromium.launch(headless=True)
                context = await browser.new_context()
                await context.add_cookies([{"name": "beaver_auth", "value": token,
                                           "url": origin, "httpOnly": True,
                                           "secure": False, "sameSite": "Strict"}])
                page = await context.new_page()
                errors = []
                page.on("pageerror", lambda error: errors.append(type(error).__name__))
                statuses = []
                page.on("response", lambda response: statuses.append(
                    (response.url.removeprefix(origin), response.status))
                    if "/auth/webauthn/register/" in response.url else None)
                cdp = await context.new_cdp_session(page)
                await cdp.send("WebAuthn.enable")
                await cdp.send("WebAuthn.addVirtualAuthenticator", {"options": {
                    "protocol": "ctap2", "transport": "internal",
                    "hasResidentKey": True, "hasUserVerification": True,
                    "isUserVerified": True, "automaticPresenceSimulation": True,
                }})
                await page.goto(origin)
                assert await page.evaluate("window.isSecureContext")
                result = await asyncio.wait_for(page.evaluate(
                    "([email, name]) => window.registerSecurityPasskey(email, name)",
                    [EMAIL, f"  {NAME}  "]), timeout=30)
                assert result == {"status": "success"}, result
                assert statuses == [("/auth/webauthn/register/begin", 200),
                                    ("/auth/webauthn/register/complete", 200)], statuses
                assert not errors, errors
                assert await page.evaluate("localStorage.length === 0 && sessionStorage.length === 0")
                cookies = await context.cookies()
                assert any(c["name"] == "beaver_auth" and c["httpOnly"] for c in cookies)
                version = browser.version
                await browser.close()
                browser = None

            after = await client.get("/auth/webauthn/credentials", headers=headers)
            assert after.status_code == 200
            listed = after.json()
            assert len(listed) == 1 and listed[0]["name"] == NAME

            # Independent SQLite connection establishes persistence, rather than
            # merely checking an ORM identity map or the completion response.
            with sqlite3.connect(folder / "acceptance.db") as connection:
                rows = connection.execute(
                    "SELECT name, user_id, length(public_key) FROM webauthn_credential"
                ).fetchall()
                assert len(rows) == 1
                assert rows[0][0] == NAME
                assert uuid.UUID(str(rows[0][1])).hex == owner
                assert rows[0][2] > 0
                challenges = connection.execute("SELECT count(*) FROM webauthn_challenge").fetchone()[0]
                assert challenges == 0
            return {"status": "PASS", "chromium": version,
                    "registration_http": [200, 200], "javascript_result": result,
                    "credential_list_http": after.status_code,
                    "persisted_credentials": len(rows), "name_roundtrip": True,
                    "public_key_persisted": True, "remaining_challenges": challenges,
                    "browser_storage_empty": True, "page_errors": len(errors),
                    "real_verifier": True, "auth_dependency_overrides": False,
                    "temporary_database": True,
                    "limitations": ["Minimal HTML shell, not full NiceGUI Security page or loader",
                                    "Virtual CTAP2 authenticator, not hardware/iOS",
                                    "List endpoint uses real server-created bearer session; registration uses HttpOnly cookie",
                                    "No full-app middleware, proxy, login, negative-path, or deployment coverage"]}
    finally:
        if browser is not None:
            await browser.close()
        server.should_exit = True
        await task
        sock.close()
        await db.engine.dispose()


def main():
    with tempfile.TemporaryDirectory(prefix="security-webauthn-acceptance-") as temp:
        result = asyncio.run(acceptance(Path(temp)))
    result["temporary_database_removed"] = not Path(temp).exists()
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
