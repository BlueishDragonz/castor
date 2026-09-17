"""Standalone full-app legacy email recovery acceptance; disposable state only.

Run: .venv/bin/python tests/security_recovery_browser_acceptance.py
The sole substituted application boundary is SMTP_SSL transport. Real routes,
views, mail construction, password hashing, and authentication remain untouched.
No screenshots, traces, request logs, credentials, or reset URLs are emitted.
"""
from __future__ import annotations

import asyncio
from email import policy
from email.parser import BytesParser
import json
import logging
import os
from pathlib import Path
import re
import secrets
import smtplib
import socket
import sqlite3
import subprocess
import sys
import tempfile
from urllib.parse import urlsplit, parse_qs

ROOT = Path(__file__).resolve().parents[1]
EMAIL = "legacy-recovery-acceptance@example.com"
NOTICE = "If the account can be recovered, reset instructions will be emailed. Please check your inbox."


def serve(folder: Path, fd: int) -> None:
    """Run unmodified main.app; intercept only SMTP transport and log sinks."""
    os.umask(0o077)
    os.chdir(folder)
    sys.path.insert(0, str(ROOT))
    capture = folder / "mail"

    class CaptureSMTPSSL:
        def __init__(self, host, port, *, timeout):
            assert host == "smtp.gmail.com" and port == 465 and timeout == 10

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def login(self, sender, password):
            assert sender == os.environ["SMTP_EMAIL_USERNAME"]
            assert password == os.environ["SMTP_EMAIL_PASSWORD"]

        def sendmail(self, sender, recipients, message):
            assert sender == os.environ["SMTP_EMAIL_USERNAME"]
            assert recipients == [EMAIL], "SMTP capture refuses non-fixture recipients"
            destination = capture / (secrets.token_hex(12) + ".eml")
            pending = destination.with_suffix(".pending")
            with pending.open("xb") as stream:
                stream.write(message.encode("utf-8"))
            pending.replace(destination)
            return {}

    smtplib.SMTP_SSL = CaptureSMTPSSL
    # The real reset-success path logs a partial auth token. Never format or
    # retain any app log text: record severity alone, including exceptions.
    from loguru import logger
    logger.remove()

    def safe_log(message):
        if message.record["level"].no >= 40:
            with (folder / "server-errors").open("ab") as stream:
                stream.write(b"error\n")

    logger.add(safe_log, format="{level}", diagnose=False, backtrace=False)

    class SafeHandler(logging.Handler):
        def emit(self, record):
            if record.levelno >= logging.ERROR:
                with (folder / "server-errors").open("ab") as stream:
                    stream.write(b"error\n")

    logging.basicConfig(handlers=[SafeHandler()], level=logging.WARNING, force=True)
    from beaverhabits.main import app
    from beaverhabits.configs import settings
    import uvicorn

    assert settings.DATABASE_URL == os.environ["DATABASE_URL"]
    assert settings.APP_URL == os.environ["APP_URL"]
    assert not settings.TRUSTED_EMAIL_HEADER and not settings.TRUSTED_LOCAL_EMAIL
    assert not app.dependency_overrides
    assert smtplib.SMTP_SSL is CaptureSMTPSSL
    sock = socket.socket(fileno=fd)
    config = uvicorn.Config(app, log_config=None, access_log=False, log_level="error")
    asyncio.run(uvicorn.Server(config).serve(sockets=[sock]))


def environment(folder: Path, origin: str) -> dict[str, str]:
    # Do not inherit application credentials/configuration from the host shell.
    env = {key: os.environ[key] for key in (
        "PATH", "HOME", "LANG", "LC_ALL", "VIRTUAL_ENV", "PLAYWRIGHT_BROWSERS_PATH"
    ) if key in os.environ}
    env.update({
        "PYTHON_DOTENV_DISABLED": "1", "PYTHONDONTWRITEBYTECODE": "1",
        "DATABASE_URL": f"sqlite+aiosqlite:///{folder / 'acceptance.db'}",
        "JWT_SECRET": secrets.token_urlsafe(48),
        "RESET_PASSWORD_TOKEN_SECRET": secrets.token_urlsafe(48),
        "NICEGUI_STORAGE_SECRET": secrets.token_urlsafe(48),
        "JWT_LIFETIME_SECONDS": "600", "APP_URL": origin,
        "WEBAUTHN_ORIGIN": origin, "WEBAUTHN_RP_ID": "localhost",
        "CSRF_ALLOWED_ORIGINS": json.dumps([origin]),
        "TRUSTED_EMAIL_HEADER": "", "TRUSTED_LOCAL_EMAIL": "",
        "SENTRY_DSN": "", "ENV": "dev", "DEBUG": "false",
        "ENABLE_DAILY_BACKUP": "false", "ENABLE_PLAN": "false",
        "REQUIRE_ADMIN_FOR_REGISTRATION": "false", "TLS_TERMINATED": "false",
        "GOOGLE_ONE_TAP_ENABLED": "false", "UMAMI_ANALYTICS_ID": "",
        "HIGHLIGHT_KEY": "", "HIGHLIGHT_IO_PROJECT_ID": "",
        "NICEGUI_STORAGE_PATH": str(folder / ".nicegui"),
        "SMTP_EMAIL_USERNAME": "capture-sender@example.com",
        "SMTP_EMAIL_PASSWORD": secrets.token_urlsafe(32),
    })
    return env


async def captured_link(folder: Path, origin: str) -> str:
    for _ in range(200):
        messages = list((folder / "mail").glob("*.eml"))
        if messages:
            assert len(messages) == 1, "Expected exactly one SMTP message"
            message_file = messages[0]
            assert message_file.stat().st_mode & 0o077 == 0
            message = BytesParser(policy=policy.default).parsebytes(message_file.read_bytes())
            assert message["To"] == EMAIL
            assert message["Subject"] == "Reset your password"
            assert message.get_content_type() == "text/plain"
            links = re.findall(r"https?://\S+", message.get_content())
            assert len(links) == 1
            link = links[0]
            parsed = urlsplit(link)
            assert parsed.scheme + "://" + parsed.netloc == origin
            assert parsed.path == "/reset-password"
            assert set(parse_qs(parsed.query)) == {"token"}
            return link
        await asyncio.sleep(0.05)
    raise RuntimeError("SMTP message not captured")


async def acceptance(folder: Path, result: dict) -> None:
    import httpx
    from playwright.async_api import async_playwright, expect

    (folder / "statics").symlink_to(ROOT / "statics", target_is_directory=True)
    (folder / "mail").mkdir(mode=0o700)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    origin = f"http://localhost:{sock.getsockname()[1]}"
    process = None
    errors = {"page_errors": 0, "console_errors": 0}

    def observe(page):
        page.set_default_timeout(20000)

        def page_error(_error):
            errors["page_errors"] += 1

        def console(message):
            if message.type == "error":
                errors["console_errors"] += 1
                # Fixed diagnostic labels only; never emit console text/URLs.
                category = next((label for marker, label in (
                    ("ERR_CERT_AUTHORITY_INVALID", "certificate"),
                    ("Content Security Policy", "csp"),
                    ("404", "http_404"),
                    ("ERR_CONNECTION", "connection"),
                    ("Method", "method"),
                ) if marker in message.text), "other")
                counts = result.setdefault("console_error_categories", {})
                counts[category] = counts.get(category, 0) + 1

        def response_status(response):
            if response.status >= 400:
                path = urlsplit(response.url).path
                # Output only allowlisted static paths or a fixed reset label.
                label = path if path.startswith('/statics/') and re.fullmatch(
                    r'/statics/[A-Za-z0-9_./-]+', path
                ) else ('reset-page' if path == '/reset-password' else path if path in ('/favicon.ico', '/favicon.png', '/manifest.json', '/service-worker.js') else 'other')
                failures = result.setdefault('browser_http_failures', {})
                failures[label] = response.status
                # Diagnostic URL: omit query/fragment, which can carry reset secrets.
                parsed = urlsplit(response.url)
                result.setdefault('failed_resource_urls', []).append(
                    parsed.scheme + '://' + parsed.netloc + parsed.path
                )

        page.on("pageerror", page_error)
        page.on("console", console)
        page.on("response", response_status)

    def snapshot():
        with sqlite3.connect(folder / "acceptance.db") as connection:
            row = connection.execute(
                "SELECT hashed_password, token_version FROM user WHERE email = ?", (EMAIL,)
            ).fetchone()
            assert row is not None
            return row

    try:
        result["stage"] = "start isolated full application"
        process = subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), "--serve", str(folder), str(sock.fileno())],
            cwd=folder, env=environment(folder, origin), pass_fds=(sock.fileno(),),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        async with httpx.AsyncClient(base_url=origin, trust_env=False) as client:
            for _ in range(300):
                assert process.poll() is None, "Server exited before readiness"
                try:
                    if (await client.get("/health", timeout=0.5)).status_code == 200:
                        break
                except httpx.TransportError:
                    pass
                await asyncio.sleep(0.05)
            else:
                raise RuntimeError("Application readiness timed out")
            result["stage"] = "create disposable account via real registration API"
            original = secrets.token_urlsafe(40)
            replacement = secrets.token_urlsafe(40)
            replay_password = secrets.token_urlsafe(40)
            registered = await client.post("/auth/register", json={"email": EMAIL, "password": original})
            result["registration_http"] = registered.status_code
            assert registered.status_code == 201
            login = await client.post("/auth/login", data={"username": EMAIL, "password": original})
            result["initial_login_http"] = login.status_code
            assert login.status_code == 200
            old_token = login.json()["access_token"]
            old_headers = {"Authorization": "Bearer " + old_token}
            assert (await client.get("/users/me", headers=old_headers)).status_code == 200
            before = snapshot()
            # Raw auth registration does not run GUI habit-list onboarding.
            # Supply the non-auth data fixture a GUI-created account already has.
            with sqlite3.connect(folder / "acceptance.db") as connection:
                connection.execute(
                    "INSERT INTO habit_list (user_id, data, created_at, updated_at) "
                    "SELECT id, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP FROM user WHERE email = ?",
                    (json.dumps({"habits": []}), EMAIL),
                )
            result["habit_list_fixture_initialized"] = True

            async with async_playwright() as playwright:
                browser = await playwright.chromium.launch(headless=True)
                try:
                    result["chromium"] = browser.version
                    context = await browser.new_context()
                    page = await context.new_page()
                    observe(page)
                    result["stage"] = "request legacy reset through actual login UI"
                    response = await page.goto(origin + "/login")
                    assert response and response.status == 200
                    await page.get_by_label("Email", exact=True).fill(EMAIL)
                    await page.get_by_role("button", name="Continue", exact=True).click()
                    await page.get_by_role("link", name="Forgot password?", exact=True).click()
                    await expect(page.get_by_text(NOTICE, exact=True)).to_be_visible()
                    link = await captured_link(folder, origin)
                    result["smtp_messages"] = 1
                    result["real_mime_verified"] = True

                    # Two independently loaded pages retain the issued snapshot.
                    # Replaying the stale callback is stricter than a fresh GET.
                    result["stage"] = "open captured link in two isolated browser contexts"
                    replay_context = await browser.new_context()
                    replay = await replay_context.new_page()
                    observe(replay)
                    for target in (page, replay):
                        response = await target.goto(link)
                        assert response and response.status == 200
                        await expect(target.get_by_label("New password", exact=True)).to_be_visible()
                    result["stage"] = "submit valid password through actual reset UI"
                    await page.get_by_label("New password", exact=True).fill(replacement)
                    await page.get_by_label("Confirm password", exact=True).fill(replacement)
                    await page.get_by_role("button", name="Continue", exact=True).click()
                    await page.wait_for_url(lambda url: urlsplit(url).path.startswith("/gui"))
                    after = snapshot()
                    assert after[0] != before[0] and after[1] == before[1] + 1
                    result["password_reset_ui"] = True
                    result["token_version_increment"] = 1

                    result["stage"] = "verify new login and old session rejection"
                    fresh = await client.post("/auth/login", data={"username": EMAIL, "password": replacement})
                    result["new_password_login_http"] = fresh.status_code
                    assert fresh.status_code == 200
                    fresh_headers = {"Authorization": "Bearer " + fresh.json()["access_token"]}
                    assert (await client.get("/users/me", headers=fresh_headers)).status_code == 200
                    revoked = await client.get("/users/me", headers=old_headers)
                    result["old_session_http"] = revoked.status_code
                    assert revoked.status_code == 401
                    old_login = await client.post("/auth/login", data={"username": EMAIL, "password": original})
                    result["old_password_login_http"] = old_login.status_code
                    assert old_login.status_code == 400

                    result["stage"] = "reject stale browser reset callback safely"
                    await replay.get_by_label("New password", exact=True).fill(replay_password)
                    await replay.get_by_label("Confirm password", exact=True).fill(replay_password)
                    await replay.get_by_role("button", name="Continue", exact=True).click()
                    await expect(replay.get_by_text(
                        "This reset link is invalid or expired. Please request a new one.", exact=True
                    )).to_be_visible()
                    assert snapshot() == after
                    assert not any(c["name"] == "beaver_auth" for c in await replay_context.cookies())
                    result["stale_callback_rejected"] = True
                    result["stage"] = "reject fresh navigation with consumed reset link"
                    replay_response = await replay.goto(link)
                    result["replay_navigation_http"] = replay_response.status if replay_response else None
                    result["replay_redirected_to_login"] = urlsplit(replay.url).path == "/login"
                    assert snapshot() == after
                    result["replay_did_not_mutate_account"] = True
                    assert (await client.get("/users/me", headers=fresh_headers)).status_code == 200
                    rejected = await client.post("/auth/login", data={"username": EMAIL, "password": replay_password})
                    assert rejected.status_code == 400
                    result["replay_password_login_http"] = rejected.status_code
                    result["stage"] = "check browser acceptance constraints"
                    storage_checks = []
                    for target in (page, replay):
                        # The real login page deliberately remembers email history.
                        # Allow that exact non-credential key, never auth tokens.
                        storage_checks.append(await target.evaluate("""() =>
                            Object.keys(localStorage).every(k => k === 'bh_emails') &&
                            Object.keys(sessionStorage).every(k =>
                            ['__nicegui_tab_id', '__nicegui_tab_closed'].includes(k))"""))
                    result["no_browser_credential_storage"] = all(storage_checks)
                    result.update(errors)
                    result["server_errors"] = (folder / "server-errors").exists()
                    blockers = []
                    if not result["replay_redirected_to_login"]:
                        blockers.append("Consumed-link navigation is not safely redirected")
                    if result["replay_navigation_http"] >= 500:
                        blockers.append("Consumed-link navigation returns server error")
                    if any(errors.values()):
                        blockers.append("Browser errors present")
                    if not all(storage_checks):
                        blockers.append("Unexpected browser storage keys")
                    if result["server_errors"]:
                        blockers.append("Server logged errors")
                    result["blockers"] = blockers
                    assert not blockers, "Acceptance constraints failed"
                finally:
                    await browser.close()
            result["server_errors"] = (folder / "server-errors").exists()
            assert not result["server_errors"]
            result.update(status="PASS", stage="complete", full_app=True,
                          uvicorn_subprocess=True, auth_dependency_overrides=False,
                          smtp_transport_only_stub=True,
                          passwordless_account_tested=False)
    finally:
        result.update(errors)
        if process is not None:
            process.terminate()
            try:
                await asyncio.to_thread(process.wait, timeout=20)
            except subprocess.TimeoutExpired:
                process.kill()
                await asyncio.to_thread(process.wait)
            result["server_stopped"] = process.poll() is not None
        sock.close()


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "--serve":
        serve(Path(sys.argv[2]), int(sys.argv[3]))
        return 0
    result = {"status": "FAIL", "temporary_database": True,
              "full_app": True, "uvicorn_subprocess": True,
              "smtp_transport_only_stub": True, "passwordless_account_tested": False}
    cwd = Path.cwd()
    with tempfile.TemporaryDirectory(prefix="security-recovery-acceptance-") as temp:
        try:
            asyncio.run(acceptance(Path(temp), result))
        except Exception as error:
            # Playwright exception messages may contain secret URLs/field values.
            result["error_type"] = type(error).__name__
        finally:
            os.chdir(cwd)
    result["temporary_database_removed"] = not Path(temp).exists()
    result["cwd_unchanged"] = Path.cwd() == cwd
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
