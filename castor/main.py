import asyncio
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from castor.app.app import init_auth_routes
from castor.app.db import async_session_maker, create_db_and_tables
from castor.app.reset_routes import router as reset_router
from castor.configs import settings
from castor.logger import logger
from castor.app.admin_routes import router as admin_router
from castor.routes.api import init_api_routes
from castor.routes.metrics import init_metrics_routes
from castor.scheduler import daily_backup_task

logger.info("Starting Castor...")


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Validate configuration
    if settings.REQUIRE_ADMIN_FOR_REGISTRATION and not settings.ADMIN_EMAIL:
        raise RuntimeError(
            "ADMIN_EMAIL must be set when REQUIRE_ADMIN_FOR_REGISTRATION is enabled"
        )

    # Security: fail fast if critical secrets are using defaults or empty
    if settings.JWT_SECRET == "SECRET" or not settings.JWT_SECRET:
        raise RuntimeError("JWT_SECRET must be set to a strong random value (not 'SECRET')")
    if not settings.RESET_PASSWORD_TOKEN_SECRET:
        raise RuntimeError("RESET_PASSWORD_TOKEN_SECRET must be set")
    if settings.NICEGUI_STORAGE_SECRET == "dev" or not settings.NICEGUI_STORAGE_SECRET:
        raise RuntimeError("NICEGUI_STORAGE_SECRET must be set to a strong random value (not 'dev')")

    # Enable warning msg
    if settings.DEBUG:
        logger.info("Debug mode enabled")
        loop = asyncio.get_running_loop()
        loop.set_debug(True)
        loop.slow_callback_duration = 0.01

    # Create new database and tables if they don't exist
    await create_db_and_tables()

    from castor.demo_seed import init_demo_seed_task
    from castor.integrity import daily_integrity_task
    from castor.app.audit import audit_retention_task
    tasks = [asyncio.create_task(daily_integrity_task()),
             asyncio.create_task(audit_retention_task()),
             asyncio.create_task(init_demo_seed_task())]
    if settings.ENABLE_DAILY_BACKUP:
        tasks.append(asyncio.create_task(daily_backup_task()))
    try:
        yield
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        # F2: flush habit lists that are still sitting in the in-memory cache.
        #
        # Writes are acknowledged to the client as soon as the in-memory
        # object is mutated, but reach SQLite on a debounce. On SIGTERM the
        # process would otherwise exit with up to 5s of user-visible,
        # already-confirmed writes still unpersisted — and nothing anywhere
        # recorded that the loss happened.
        #
        # This runs in the lifespan `finally`, which uvicorn executes on
        # graceful shutdown, so it only helps if gunicorn actually receives
        # SIGTERM. The Dockerfile CMD was `exec node ...`, which discarded the
        # shell's trap and got gunicorn SIGKILLed instead; docker/entrypoint.sh
        # fixes that half.
        try:
            from castor.storage import get_user_dict_storage

            await get_user_dict_storage().flush_all()
            logger.info("Flushed pending habit-list writes on shutdown")
        except Exception as exc:  # noqa: BLE001 - never block shutdown
            logger.error(f"Failed to flush pending writes on shutdown: {exc}")


app = FastAPI(lifespan=lifespan)


@app.get("/health", response_class=PlainTextResponse, include_in_schema=False)
async def health_check():
    """Readiness: asserts the dependency the service actually needs.

    F8: this used to return a literal "OK" without touching anything, so it
    reported healthy while gunicorn was dead, the SQLite file was corrupt or
    locked, or the data volume had failed to mount. Because Docker's
    `restart: unless-stopped` keys off this, that made every real outage
    invisible and unmonitored.

    A trivial `SELECT 1` is the cheapest possible proof that the database is
    reachable and answering. It is deliberately not a deep integrity check —
    that belongs on a schedule, not on a 30s poll.
    """
    try:
        async with async_session_maker() as session:
            await session.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        # Never leak connection strings or driver detail to the caller.
        logger.error(f"Readiness check failed: {exc.__class__.__name__}")
        return PlainTextResponse("UNAVAILABLE", status_code=503)
    return PlainTextResponse("OK")


@app.get("/live", response_class=PlainTextResponse, include_in_schema=False)
async def liveness_check():
    """Liveness: the process is running and can serve HTTP.

    Deliberately static. A liveness probe that depends on the database would
    restart the container during a transient database outage, turning a
    recoverable blip into a crash-loop.
    """
    return PlainTextResponse("OK")


if settings.is_dev():
    from castor.demo_seed import seed_demo_account

    @app.post("/dev/reseed-demo", include_in_schema=False)
    async def reseed_demo():
        """Dev-only endpoint: re-create the demo account + 5 habits + tick history.

        Idempotent: if the demo user already exists with the canonical password
        and all 5 habits, this is a no-op. Returns a status dict.

        Only available in dev environments (settings.ENV == "dev"). Refuses
        in production.
        """
        result = await seed_demo_account()
        return result

# auth
if settings.DEBUG:
    # In dev mode, expose metrics on main port for convenience
    init_metrics_routes(app)
else:
    # In production, metrics should be on internal-only listener (127.0.0.1:9090)
    # This is handled by running a second gunicorn instance on the internal port
    # with a separate app factory that only includes metrics routes.
    logger.info("Production mode: /metrics and /debug/* should be served on internal port 9090 only")
    # For now, we disable them on the public port
    pass

init_auth_routes(app)
app.include_router(reset_router)
init_api_routes(app)
app.include_router(admin_router)
# Paid-plan / landing-page surface (Paddle billing, statics/astro mount) was
# removed at slice 30: ENABLE_PLAN was never enabled in production and the
# Paddle integration was dropped during the migration (parity 12.3). The
# ENABLE_PLAN setting and the PADDLE_* env vars remain in configs.py as
# upstream-merge ballast; nothing reads them.

from castor.app.http_security import BrowserOriginMiddleware
from castor.app.rate_limits import IPRateLimitMiddleware
app.add_middleware(IPRateLimitMiddleware)
app.add_middleware(BrowserOriginMiddleware, allowed_origins=settings.CSRF_ALLOWED_ORIGINS)


if settings.SENTRY_DSN:
    logger.info("Setting up Sentry...")
    # F25: `sentry_sdk` is an OPTIONAL dependency but was imported inside this
    # `if`, so a configured SENTRY_DSN on a venv without the package crashed
    # the process at import time — a boot failure with no useful message,
    # triggered by turning on error reporting. Optional integrations must fail
    # soft and visibly, never take the app down.
    #
    # `send_default_pii=True` is also wrong for this app: it forwards request
    # bodies, headers and user IPs to a third party, which conflicts with the
    # privacy posture the audit recorded in F11 (no PII in logs). It is now
    # opt-in, because enabling telemetry should not silently start exporting
    # personal data.
    try:
        import sentry_sdk
    except ImportError:
        logger.error(
            "SENTRY_DSN is set but the sentry-sdk package is not installed. "
            "Install it with `uv sync --extra sentry` or add it to your "
            "dependency group, or clear SENTRY_DSN to silence this. "
            "Continuing WITHOUT error reporting."
        )
    else:
        sentry_sdk.init(
            settings.SENTRY_DSN,
            # F11: default off. Habit data and emails are personal; sending
            # them to a third party must be an explicit choice.
            send_default_pii=settings.SENTRY_SEND_PII,
        )


@app.middleware("http")
async def durable_writes(request: Request, call_next):
    """Make every acknowledged habit mutation durable before responding (F2).

    F2 was that the API returned success as soon as the in-memory habit list
    was mutated, while the write to SQLite happened on a debounce. A restart
    inside that window silently discarded writes the user had already been
    told were saved.

    This is deliberately a single middleware choke point rather than an
    explicit `await flush()` in each handler: a new mutating endpoint added
    later would otherwise inherit the old, unsafe behaviour by omission. Every
    non-GET request flushes here, and `flush()` is a no-op when nothing is
    dirty, so reads and unrelated writes cost nothing.

    A flush failure must NOT be reported as success — the user would again be
    told a write was saved when it was not.
    """
    response = await call_next(request)
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return response

    try:
        from castor.storage import get_user_dict_storage

        await get_user_dict_storage().flush_all()
    except Exception as exc:  # noqa: BLE001
        logger.error(f"Durability flush failed for {request.method} {request.url.path}: {exc}")
        if response.status_code < 400:
            return JSONResponse(
                status_code=503,
                content={"detail": "Could not save your change. Please retry."},
            )
    return response


@app.middleware("http")
async def Digest(request: Request, call_next):
    start_time = time.perf_counter()
    response = await call_next(request)
    process_time = (time.perf_counter() - start_time) * 1000
    response.headers["X-Process-Time"] = str(process_time)
    logger.info(
        f"DIGEST {request.method} {request.url.path} {response.status_code} {process_time:.0f}ms"
    )
    return response


@app.middleware("http")
async def security_headers(request: Request, call_next):
    """Add security headers to all responses."""
    response = await call_next(request)
    
    # HSTS - only in production (non-dev)
    if not settings.is_dev():
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    
    # CSP - allow ws: for WebSocket on HTTP connections, and allow eval/blob for module loading
    # Check if we're likely behind HTTP (no TLS) - allow ws: for NiceGUI WebSocket
    # In production with TLS termination, HSTS will enforce HTTPS and wss: is sufficient
    if settings.is_dev() or not getattr(settings, 'TLS_TERMINATED', False):
        csp = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' 'unsafe-eval' https://accounts.google.com https://cdn.paddle.com; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: https:; "
            "font-src 'self' data:; "
            "connect-src 'self' https://accounts.google.com https://api.telegram.org ws: wss:; "
            "frame-src https://accounts.google.com; "
            "form-action 'self'; "
            "base-uri 'self'; "
            "frame-ancestors 'none'; "
            "worker-src 'self' blob:"
        )
    else:
        csp = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' 'unsafe-eval' https://accounts.google.com https://cdn.paddle.com; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: https:; "
            "font-src 'self' data:; "
            "connect-src 'self' https://accounts.google.com https://api.telegram.org wss:; "
            "frame-src https://accounts.google.com; "
            "form-action 'self'; "
            "base-uri 'self'; "
            "frame-ancestors 'none'; "
            "worker-src 'self' blob:"
        )
    response.headers["Content-Security-Policy"] = csp
    
    # Other security headers
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    
    return response


if __name__ == "__main__":
    import uvicorn

    if settings.DEBUG:
        # start fastapi app
        logger.info("Starting in debug mode")
        uvicorn.run(app=app, host="0.0.0.0", port=9001, workers=1)
    else:
        raise RuntimeError("This script should not be run directly in production.")
