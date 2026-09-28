import datetime
import logging
import os
from enum import Enum
from urllib.parse import urlparse as new_url

import dotenv
import pytz
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings

logging.getLogger("niceGUI").setLevel(logging.INFO)
logger = logging.getLogger("castor.configs")

# The development default for FRONTEND_URL. Named so that public_origin()
# can tell "the operator set this" from "nobody set anything", which is
# what keeps the legacy APP_URL / TLS_TERMINATED fallbacks reachable.
DEFAULT_FRONTEND_URL = "http://localhost:4321"

# Fallback WebAuthn relying party, used only when no public origin is
# declared. Named so resolve_webauthn_settings() can tell "the operator set
# this" from "this is the fallback" and derive only in the latter case.
DEFAULT_WEBAUTHN_RP_ID = "localhost"
DEFAULT_WEBAUTHN_ORIGIN = "http://localhost:8080"

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
    FRONTEND_URL: str = DEFAULT_FRONTEND_URL

    # ─── Public origin (single source of truth) ───────────────────────
    # The externally visible application URL. When set, it is the ONE
    # value that decides:
    #   - the Secure flag on session + WebAuthn browser cookies
    #   - whether the deployment is treated as HTTPS for CSP/HSTS
    #   - the CSRF origin allowlist
    #
    # It deliberately takes priority over APP_URL, which is a leftover
    # from the SaaS-era config and which nothing else in the codebase
    # reads. An operator who set FRONTEND_URL=https://castor.example and
    # left APP_URL empty previously got a session cookie WITHOUT Secure,
    # because the cookie logic only consulted APP_URL/TLS_TERMINATED —
    # a silent downgrade of the app's stated "Strict + Secure in
    # production" intent, arrived at by configuring the setting the docs
    # actually tell you to set.
    #
    # Leave empty to keep the legacy behaviour (derive from APP_URL and
    # TLS_TERMINATED). Setting it is the recommended path for any
    # deployment that will sit behind a TLS-terminating reverse proxy.
    PUBLIC_URL: str = ""

    @property
    def public_url(self) -> str:
        """The operator-declared public origin, or '' when unconfigured."""
        return (self.PUBLIC_URL or "").strip().rstrip("/")

    def is_https_public(self) -> bool:
        """True when the deployment is declared to be served over HTTPS.

        Consulted for the cookie Secure flag and for HTTPS-only policy.
        Never derived from a request header: an attacker-supplied
        X-Forwarded-Proto must not be able to decide this.

        Reads the same declared origin that `public_origin()` and the
        WebAuthn derivation use, so the cookie's Secure flag, the CSRF
        allowlist and the passkey expected-origin can never disagree about
        which origin is public. TLS_TERMINATED remains honoured as a
        legacy override for deployments that have not yet set a public
        origin.
        """
        origin = self.public_origin()
        if origin:
            return origin.lower().startswith("https://")
        return (self.APP_URL or "").startswith("https://") or self.TLS_TERMINATED

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
        # PUBLIC_URL is the operator-declared public origin. Including it
        # keeps the CSRF allowlist and the cookie Secure decision derived
        # from one setting, so a deployment cannot end up with a Secure
        # cookie and an origin allowlist that disagree about its own URL.
        declared = self.public_url
        if declared and declared not in origins:
            origins.append(declared)

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

    # ─── Reverse-proxy trust boundary ─────────────────────────────────
    # Whether to honour X-Forwarded-Proto / X-Forwarded-Host from a
    # trusted reverse proxy (Caddy, nginx, Traefik).
    #
    # DEFAULT FALSE, and the default must stay false. These headers are
    # client-supplied unless a proxy is guaranteed to overwrite them, and
    # an attacker who can set X-Forwarded-Proto: https on a plain-HTTP
    # deployment would otherwise flip the app into believing it is on TLS
    # — which decides the Secure cookie flag, the HSTS header and the
    # WebAuthn expected origin. That is an origin-confusion primitive, not
    # a convenience.
    #
    # Turn it on ONLY when the app is reachable exclusively through a
    # proxy you control that strips client-supplied forwarding headers and
    # sets its own. The app still binds loopback in that topology, so the
    # proxy is the only path in and the header cannot be spoofed.
    #
    # The app does not need this to work behind Caddy today: it takes
    # its public scheme/host from WEBAUTHN_ORIGIN / FRONTEND_URL / APP_URL,
    # which are operator-declared rather than header-derived. This setting
    # exists for the case where an operator wants request-derived URLs
    # (request.url, absolute redirects) to reflect the public origin.
    TRUST_PROXY_HEADERS: bool = False
    # Hop count / CIDR list handed to uvicorn's --forwarded-allow-ips.
    # Empty means "trust nothing" and is the safe default. `*` is accepted
    # by uvicorn but should never be used: it re-enables spoofing from any
    # client that can reach the socket directly.
    TRUSTED_PROXY_IPS: str = ""

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
    #
    # WEBAUTHN_RP_ID and WEBAUTHN_ORIGIN are *defaults*, not the source of
    # truth. Both are derived from the declared public origin at startup
    # (see resolve_webauthn_settings) unless explicitly overridden, so a
    # deployment cannot end up asserting an RP ID that does not match the
    # host the browser is actually on — the failure mode where every
    # ceremony is rejected with "The operation is insecure" and the UI
    # looks like the Add button is simply broken.
    #
    # The fallbacks below are deliberately NOT empty. Two reasons:
    #   - An empty RP ID makes py-webauthn raise "rp_id cannot be an empty
    #     string" on the first ceremony, so a code path that builds options
    #     without booting the lifespan (several tests do) would crash
    #     rather than fall back.
    #   - A bare checkout with no declared origin has to still start and
    #     serve something.
    # "localhost" is the right fallback: a WebAuthn RP ID must be a
    # registrable domain suffix, and browsers reject a bare IP literal.
    WEBAUTHN_RP_ID: str = DEFAULT_WEBAUTHN_RP_ID
    WEBAUTHN_RP_NAME: str = "Castor"
    WEBAUTHN_ORIGIN: str = DEFAULT_WEBAUTHN_ORIGIN
    WEBAUTHN_TIMEOUT: int = 60000  # 60 seconds

    def public_origin(self) -> str:
        """The declared public origin, normalised to scheme://host[:port].

        Precedence: PUBLIC_URL, then FRONTEND_URL, then the legacy APP_URL.
        Returns '' when nothing is declared, which is the correct signal for
        "derive nothing".

        FRONTEND_URL is skipped when it still holds its built-in default.
        That default ("http://localhost:4321") is a development convenience,
        not a declaration by the operator, and letting it win would mean
        `public_origin()` was never empty in practice — which silently
        killed the APP_URL and TLS_TERMINATED fallbacks that
        `is_https_public()` has always honoured, so a legacy deployment
        setting only APP_URL=https://... would have started shipping
        non-Secure cookies. An operator who genuinely serves on
        localhost:4321 gains nothing from the distinction, because the
        inbound-origin fallback covers that case.
        """
        candidates = [self.PUBLIC_URL, self.APP_URL]
        if self.FRONTEND_URL and self.FRONTEND_URL != DEFAULT_FRONTEND_URL:
            candidates.insert(1, self.FRONTEND_URL)
        for candidate in candidates:
            raw = (candidate or "").strip()
            if not raw:
                continue
            try:
                parsed = new_url(raw)
            except ValueError:
                # A malformed declared origin must not crash start-up, but
                # it must not silently become an unparseable RP ID either.
                logger.warning(
                    "Ignoring malformed public origin %r while deriving the "
                    "WebAuthn relying-party configuration",
                    raw,
                )
                continue
            if not parsed.hostname:
                logger.warning(
                    "Public origin %r has no hostname; skipping it",
                    raw,
                )
                continue
            # Reconstruct the origin: urlparse has no .origin, and we need
            # the port preserved because the dev setup serves on 4321.
            netloc = parsed.hostname
            if ":" in netloc:  # IPv6 literal
                netloc = f"[{netloc}]"
            if parsed.port:
                netloc = f"{netloc}:{parsed.port}"
            return f"{parsed.scheme}://{netloc}"
        return ""

    def resolve_webauthn_settings(self) -> None:
        """Derive WEBAUTHN_RP_ID / WEBAUTHN_ORIGIN from the public origin.

        Called once at start-up. An explicit setting always wins; the
        derivation only fills a blank.

        Why this matters concretely: WEBAUTHN_RP_ID used to default to the
        literal "localhost" while the documented dev URL is
        http://127.0.0.1:4321. The browser silently refused every ceremony
        because 127.0.0.1 is not a registrable suffix of localhost, and
        the page reported only "The operation is insecure". Deriving both
        values from one declared origin removes the class of bug where the
        RP ID and the served host drift apart.
        """
        origin = self.public_origin()
        if not origin:
            # Nothing declared: the class defaults already stand. Leave
            # them alone so an explicit override is not clobbered.
            return
        # Only fill a value the operator has not set. The class defaults
        # are non-empty (see the field comments), so "unset" has to be
        # detected by comparing against those defaults rather than by
        # testing for emptiness.
        if self.WEBAUTHN_ORIGIN in ("", DEFAULT_WEBAUTHN_ORIGIN):
            self.WEBAUTHN_ORIGIN = origin
        if self.WEBAUTHN_RP_ID in ("", DEFAULT_WEBAUTHN_RP_ID):
            self.WEBAUTHN_RP_ID = new_url(self.WEBAUTHN_ORIGIN).hostname or ""
            if not self.WEBAUTHN_RP_ID:
                logger.error(
                    "Could not derive a WebAuthn relying-party ID from %r. "
                    "Set WEBAUTHN_RP_ID explicitly, or passkeys will fail.",
                    self.WEBAUTHN_ORIGIN,
                )

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