import datetime
import logging
import os
from enum import Enum

import dotenv
import pytz
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings

logging.getLogger("niceGUI").setLevel(logging.INFO)

USER_DATA_FOLDER = ".user"
# Load secrets from .user/.secrets.env explicitly (container mounts volume at /app/.user)
dotenv.load_dotenv(os.path.join(os.getcwd(), USER_DATA_FOLDER, ".secrets.env"))
dotenv.load_dotenv()  # also load .env from cwd for any overrides


class StorageType(Enum):
    SESSION = "SESSION"
    USER_DATABASE = "DATABASE"
    USER_DISK = "USER_DISK"


class TagSelectionMode(Enum):
    SINGLE = "SINGLE"
    MULTI = "MULTI"


class Settings(BaseSettings):
    ENV: str = "dev"
    DEBUG: bool = False

    # SaaS
    APP_URL: str = ""
    SENTRY_DSN: str = ""
    # F25/F11: whether Sentry may forward request bodies, headers and IPs to a
    # third party. Default False — enabling error reporting must not silently
    # start exporting personal data. Note the `sentry-sdk` package is optional;
    # setting SENTRY_DSN without installing it logs an error and continues
    # without telemetry rather than crashing at boot.
    SENTRY_SEND_PII: bool = False
    HIGHLIGHT_KEY: str = ""
    ADMIN_EMAIL: str = ""
    UMAMI_ANALYTICS_ID: str = ""
    UMAMI_SCRIPT_URL: str = "https://cloud.umami.is/script.js"
    ENABLE_PLAN: bool = False
    MAX_HABIT_COUNT: int = 5
    PADDLE_SANDBOX: bool = True
    PADDLE_CLIENT_SIDE_TOKEN: str = ""
    PADDLE_API_TOKEN: str = ""
    PADDLE_PRODUCT_ID: str = ""
    PADDLE_PRICE_ID: str = ""
    PADDLE_CALLBACK_KEY: str = ""
    HIGHLIGHT_IO_PROJECT_ID: str = ""

    # Email
    SMTP_EMAIL_USERNAME: str = ""
    SMTP_EMAIL_PASSWORD: str = ""
    # Slice 23c: previously hard-coded to smtp.gmail.com:465 in
    # castor/utils.py::send_email. Empty SMTP_HOST keeps the legacy
    # behaviour so existing single-user Gmail setups work unchanged;
    # any non-empty value triggers the new env-driven path (host +
    # port + optional STARTTLS).
    SMTP_HOST: str = ""
    SMTP_PORT: int = 587
    SMTP_FROM: str = ""
    SMTP_USE_TLS: bool = True

    # NiceGUI
    NICEGUI_STORAGE_SECRET: str = "dev"
    GUI_MOUNT_PATH: str = "/gui"
    DEMO_MOUNT_PATH: str = "/demo"
    DARK_MODE: bool | None = None

    # Storage
    HABITS_STORAGE: StorageType = StorageType.USER_DATABASE
    DATABASE_URL: str = f"sqlite+aiosqlite:///./{USER_DATA_FOLDER}/habits.db"
    MAX_USER_COUNT: int = -1
    JWT_SECRET: str = "SECRET"
    JWT_LIFETIME_SECONDS: int = 0

    # Lane 2 security (per-minute fixed windows; native clients need no CSRF token).
    CSRF_ALLOWED_ORIGINS: list[str] = []
    API_RATE_USER_PER_MINUTE: int = Field(default=120, ge=1, le=10000)
    API_RATE_IP_PER_MINUTE: int = Field(default=300, ge=1, le=10000)
    AUTH_RATE_IP_PER_MINUTE: int = Field(default=60, ge=1, le=10000)

    # Auth
    TRUSTED_EMAIL_HEADER: str = ""
    TRUSTED_LOCAL_EMAIL: str = ""
    RESET_PASSWORD_TOKEN_SECRET: str = ""
    RESET_PASSWORD_TOKEN_LIFETIME_SECONDS: int = 60 * 60  # 1 hour
    REQUIRE_ADMIN_FOR_REGISTRATION: bool = False  # Require admin auth to register users

    # Timezone: if set, overrides the browser-detected timezone for all users.
    # Use standard IANA timezone names, e.g. "America/New_York", "Europe/London", "Asia/Tokyo".
    # When not set, timezone is detected from the user's browser.
    TIME_ZONE: str = ""

    # Customization
    FIRST_DAY_OF_WEEK: int = 0  # calendar.MONDAY
    # Set to 0-6 to align today to specific day of week, e.g., 0 for Monday
    ALIGN_TODAY_TO_DAY_OF_WEEK: int | None = None
    ENABLE_IOS_STANDALONE: bool = True
    TAG_SELECTION_MODE: TagSelectionMode = TagSelectionMode.MULTI
    ENABLE_TAG_FILTERS: bool = True

    # Slice 23c: public origin of the Astro frontend. Used to build
    # join URLs in outgoing emails. Defaults to localhost for dev so a
    # fresh checkout works without extra config; in production set
    # this to the externally reachable origin (e.g. https://castor.example).
    # The Astro BFF passes X-Forwarded-Host which the email-send path
    # can fall back to if FRONTEND_URL is unset.
    FRONTEND_URL: str = "http://localhost:4321"

    def csrf_allowed_origins(self) -> list[str]:
        """Origins permitted to make state-changing requests.

        The Astro BFF forwards the *public* Origin header to the backend
        (F21: the backend's own BrowserOriginMiddleware decides CSRF). The
        backend, however, is reached on 127.0.0.1:8081, so its Host-derived
        fallback origin is the loopback address and can never match the
        public origin the BFF sent. With CSRF_ALLOWED_ORIGINS left at its
        empty default that mismatch rejected *every* browser write with a
        403, which is why an empty list must never be left to chance here.

        So: the configured list wins, and FRONTEND_URL is always included,
        because that is the origin the frontend genuinely serves from. Both
        are deduplicated and order-preserved.

        Loopback aliases: `localhost` and `127.0.0.1` (and `[::1]`) are
        the same machine but different origins to a browser, so a
        developer who opens http://127.0.0.1:4321 while FRONTEND_URL
        says http://localhost:4321 gets a 403 "Browser origin not
        allowed" on every write — sign-in included. Each loopback
        spelling of a configured loopback origin is added too.
        """
        origins = list(self.CSRF_ALLOWED_ORIGINS)
        frontend = (self.FRONTEND_URL or "").strip()
        if frontend and frontend not in origins:
            origins.append(frontend)

        # Expand http://localhost:PORT / https://localhost:PORT to the
        # equivalent 127.0.0.1 and [::1] origins.
        expanded: list[str] = list(origins)
        for origin in origins:
            for host, loopback in (
                ("localhost", "127.0.0.1"),
                ("127.0.0.1", "localhost"),
                ("localhost", "[::1]"),
            ):
                if f"://{host}:" in origin:
                    alias = origin.replace(f"://{host}:", f"://{loopback}:", 1)
                    if alias not in expanded:
                        expanded.append(alias)
        return expanded

    # TLS termination (reverse proxy with HTTPS) - set True when behind nginx/Caddy with HTTPS
    # When False (direct HTTP), CSP allows ws: for WebSocket; when True, only wss: allowed
    TLS_TERMINATED: bool = False

    INDEX_SHOW_HABIT_COUNT: bool = False
    INDEX_SHOW_HABIT_STREAK: bool = False
    INDEX_HABIT_NAME_COLUMNS: int = 5
    INDEX_HABIT_DATE_COLUMNS: int = 5
    INDEX_HABIT_DATE_REVERSE: bool = False

    HABIT_SHOW_EVERY_DAY_STREAKS: bool = False

    DAILY_NOTE_MAX_LENGTH: int = 1024
    DEFAULT_COMPLETION_STATUS_LIST: list[str] = ["yes", "no"]

    # Backup inverval(in seconds), default is oneday
    ENABLE_DAILY_BACKUP: bool = False
    DAILY_BACKUP_INTERVAL: int = 60 * 60 * 24

    # Get your Google Client ID from the Google Cloud Console.
    # See https://developers.google.com/identity/gsi/web/guides/get-google-api-clientid#get_your_google_api_client_id.
    # For local development, you should add http://localhost:8080 to the authorized JavaScript origins.
    # In production, you should add the domain of your website to the authorized JavaScript origins.
    # Make sure you include <origin>/google/auth in "Authorized redirect URIs".
    GOOGLE_ONE_TAP_CLIENT_ID: str = ""
    GOOGLE_ONE_TAP_ENABLED: bool = False
    GOOGLE_ONE_TAP_CALLBACK_URL: str = ""

    # WebAuthn / Passkeys
    WEBAUTHN_RP_ID: str = "localhost"
    WEBAUTHN_RP_NAME: str = "Castor"
    WEBAUTHN_ORIGIN: str = "http://localhost:8080"
    WEBAUTHN_TIMEOUT: int = 60000  # 60 seconds

    def is_dev(self):
        return self.ENV == "dev"

    def is_trusted_env(self):
        return self.TRUSTED_LOCAL_EMAIL

    @field_validator("TIME_ZONE")
    @classmethod
    def validate_time_zone(cls, v: str) -> str:
        if v and v not in pytz.all_timezones_set:
            raise ValueError(
                f"Invalid TIME_ZONE '{v}'. Must be a valid IANA timezone name "
                f"(e.g. 'America/New_York', 'Europe/London', 'Asia/Tokyo')."
            )
        return v


settings = Settings()