"""Domain helpers used by the FastAPI API + Astro front-end.

Surviving in P3-B (after frontend/ deletion):
  - PERIOD_TYPES, PERIOD_TYPES_FOR_HUMAN, D, W, M, Y constants
    (used by storage.storage)
  - generate_short_hash (used by views + storage.dict)
  - date_move, get_period_fist_day, timeit (used by core.completions)
  - send_email (used by app.reset_routes for password reset delivery)
  - ratelimiter decorator (legacy in-process limiter; superseded by
    app/rate_limits.py for HTTP endpoints but kept for any future
    in-process usage)

Removed in P3-B:
  - timezone helpers (fetch_user_timezone etc.) — used NiceGUI's
    app.storage.user + ui.run_javascript; Astro reads the browser
    timezone client-side
  - dark mode helpers (fetch_user_dark_mode etc.) — Quasar Dark API
    only; Astro shadcn/ui uses CSS prefers-color-scheme
  - MemoryMonitor, print_memory_snapshot — debug tooling for
    NiceGUI/Quasar runs only
  - format_date_difference — used by the NiceGUI streak page
  - hex2rgb, COLORS, parse_percentage, adjust_color_brightness —
    the 296-line Quasar color palette; nothing in the API path
    consumed these
  - get_user_today_date_sync/async, dummy_days, get_or_create_user_timezone_sync — NiceGUI helpers
  - PRIMARY_COLOR — moved out of UI; any remaining references can
    use the new Astro design tokens (see docs/design/system-spec.md)
"""
import datetime
import functools
import hashlib
import os
import smtplib
import time
from functools import wraps
from typing import Literal, TypeAlias

import pytz
from cachetools import TTLCache
from dateutil.relativedelta import relativedelta
from fastapi import HTTPException
from starlette import status

from castor.configs import settings
from castor.logger import logger

PERIOD_TYPES = D, W, M, Y = "D", "W", "M", "Y"
PERIOD_TYPES_FOR_HUMAN = {D: "Day(s)", W: "Week(s)", M: "Month(s)", Y: "Year(s)"}
PERIOD_TYPE: TypeAlias = Literal["D", "W", "M", "Y"]


def generate_short_hash(name: str) -> str:
    h = hashlib.new("sha1")
    h.update(name.encode())
    h.update(str(datetime.datetime.now()).encode())
    return h.hexdigest()[:6]


def ratelimiter(limit: int, window: int):
    if window <= 0 or window > 60 * 60:
        raise ValueError("Window must be between 1 and 3600 seconds.")
    cache = TTLCache(maxsize=128, ttl=60 * 60)

    def decorator(func):
        async def wrapper(*args, **kwargs):
            current_time = time.time()
            key = f"{args}_{kwargs}"

            # Update timestamps
            if key not in cache:
                cache[key] = [current_time]
            else:
                cache[key].append(current_time)
            cache[key] = [i for i in cache[key] if i >= current_time - window]

            # Check with threshold
            if len(cache[key]) > limit:
                logger.warning(
                    f"Rate limit exceeded for {func.__name__} with key {key}"
                )
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail=f"Rate limit exceeded. Try again later.",
                )

            return await func(*args, **kwargs)

        return wrapper

    return decorator


def get_period_fist_day(date: datetime.date, period_type: str) -> datetime.date:
    if period_type == W:
        date = date - datetime.timedelta(days=date.weekday())
    elif period_type == M:
        date = date.replace(day=1)
    elif period_type == Y:
        date = date.replace(month=1, day=1)
    return date


def date_move(
    date: datetime.date, step: int, period_type: PERIOD_TYPE
) -> datetime.date:
    if date in (datetime.date.min, datetime.date.max):
        return date

    if period_type == D:
        date = date + datetime.timedelta(days=step)
    elif period_type == W:
        date = date + datetime.timedelta(weeks=step)
    elif period_type == M:
        date = date + relativedelta(months=step)
    elif period_type == Y:
        date = date.replace(year=date.year + step)
    return date


def timeit(threshold: float):
    """
    Decorator to measure the execution time of a function and log it if it exceeds a threshold.
    """

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            start_time = time.perf_counter()
            result = func(*args, **kwargs)
            end_time = time.perf_counter()
            total_time = (end_time - start_time) * 1000  # Convert to milliseconds
            if total_time > threshold:
                logger.warning(
                    f"Function {func.__name__} took {total_time:.4f} milliseconds"
                )
            return result

        return wrapper

    return decorator


def send_email(subject: str, body: str, recipients: list[str], html_body: str | None = None):
    """Send email with optional HTML body for branding.

    Local-outbox mode (development/CI without SMTP credentials):
        Set SMTP_DEV_LOCAL_OUTBOX=/path/to/dir before launching the
        backend and the email is written to a file in that directory
        instead of being sent. Files are named with the recipient,
        timestamp, and a sha256 of the body to make finding recent
        codes easy in tests. Used heavily in migration-branch's
        forgot/reset/recovery-email integration tests; harmless in
        production (the env var is unset in deployed environments).
    """
    sender = settings.SMTP_EMAIL_USERNAME
    password = settings.SMTP_EMAIL_PASSWORD

    if html_body:
        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = sender
        msg["To"] = ", ".join(recipients)

        # Plain text fallback
        msg.attach(MIMEText(body or subject, "plain"))
        # HTML version
        msg.attach(MIMEText(html_body, "html"))
    else:
        from email.mime.text import MIMEText
        msg = MIMEText(body or subject)
        msg["Subject"] = subject
        msg["From"] = sender
        msg["To"] = ", ".join(recipients)

    outbox = os.environ.get("SMTP_DEV_LOCAL_OUTBOX")
    if outbox:
        os.makedirs(outbox, exist_ok=True)
        body_hash = hashlib.sha256(msg.as_string().encode()).hexdigest()[:10]
        recipient_tag = recipients[0].replace("@", "_at_").replace(".", "_")
        timestamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        filename = f"{timestamp}_{recipient_tag}_{body_hash}.eml"
        path = os.path.join(outbox, filename)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(msg.as_string())
        # Also write a plaintext summary so test scripts can read codes
        # via grep without parsing MIME.
        with open(path + ".summary.txt", "w", encoding="utf-8") as fh:
            fh.write(f"To: {recipients[0]}\nSubject: {subject}\n\n{body}\n")
        return path

    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=10) as smtp_server:
        smtp_server.login(sender, password)
        smtp_server.sendmail(sender, recipients, msg.as_string())
