import asyncio
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse

from beaverhabits.app.app import init_auth_routes
from beaverhabits.app.db import create_db_and_tables
from beaverhabits.app.reset_routes import router as reset_router
from beaverhabits.configs import settings
from beaverhabits.logger import logger
from beaverhabits.routes.api import init_api_routes
from beaverhabits.routes.metrics import init_metrics_routes
from beaverhabits.routes.routes import init_gui_routes
from beaverhabits.scheduler import daily_backup_task

logger.info("Starting BeaverHabits...")


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

    # Start scheduler
    if settings.ENABLE_DAILY_BACKUP:
        loop = asyncio.get_event_loop()
        loop.create_task(daily_backup_task())

    yield


app = FastAPI(lifespan=lifespan)


@app.get("/health", response_class=PlainTextResponse, include_in_schema=False)
async def health_check():
    """Always-available health check endpoint for container health checks."""
    return "OK"


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
if settings.ENABLE_PLAN:
    from beaverhabits.plan.paddle import init_paddle_routes
    from beaverhabits.routes.astro import init_astro_routes

    init_astro_routes(app)
    init_paddle_routes(app)

init_gui_routes(app)


if settings.SENTRY_DSN:
    logger.info("Setting up Sentry...")
    import sentry_sdk

    sentry_sdk.init(settings.SENTRY_DSN, send_default_pii=True)


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
    
    # CSP - restrictive but allows Google One Tap and Paddle
    csp = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' https://accounts.google.com https://cdn.paddle.com; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: https:; "
        "font-src 'self' data:; "
        "connect-src 'self' https://accounts.google.com https://api.telegram.org wss:; "
        "frame-src https://accounts.google.com; "
        "form-action 'self'; "
        "base-uri 'self'; "
        "frame-ancestors 'none'"
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
