import datetime
import hashlib
import secrets
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, EmailStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from beaverhabits.app.db import (
    PasswordResetCode,
    User,
    get_async_session_context,
    get_user_db,
)
from beaverhabits.app.users import get_user_manager, UserManager
from beaverhabits.configs import settings
from beaverhabits.logger import logger
from beaverhabits.utils import send_email

router = APIRouter(prefix="/auth", tags=["auth"])


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    email: EmailStr
    code: str  # 12 digits
    new_password: str


def _hash_code(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


def _generate_code() -> str:
    return f"{secrets.randbelow(10**12):012d}"


# Simple in-memory rate limiter per key (IP or IP:email)
# Recreated on worker restart - acceptable for this use
_rate_limit_cache: dict[str, list[float]] = {}


async def _rate_limit_check(key: str, limit: int, window: int) -> None:
    """Check rate limit for a key."""
    from cachetools import TTLCache

    global _rate_limit_cache
    current_time = datetime.datetime.now(datetime.timezone.utc).timestamp()

    if key not in _rate_limit_cache:
        _rate_limit_cache[key] = [current_time]
    else:
        _rate_limit_cache[key].append(current_time)

    # Prune old entries
    _rate_limit_cache[key] = [t for t in _rate_limit_cache[key] if t >= current_time - window]

    if len(_rate_limit_cache[key]) > limit:
        logger.warning(f"Rate limit exceeded for {key}")
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests. Try again later.",
        )


def _get_client_ip(request: Request) -> str:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


@router.post("/forgot-password")
async def forgot_password(req: ForgotPasswordRequest, request: Request):
    """Request a 12-digit reset code for the given email.

    Always returns 200 to prevent email enumeration.
    Rate limited: 3 requests per minute per IP, 1 per 15 minutes per email.
    """
    client_ip = _get_client_ip(request)

    # Rate limit by IP
    await _rate_limit_check(f"forgot_ip:{client_ip}", limit=3, window=60)
    # Rate limit by email (prevents hammering a specific address)
    await _rate_limit_check(f"forgot_email:{req.email}", limit=1, window=900)

    # Check if user exists (but don't reveal)
    async with get_async_session_context() as session:
        result = await session.execute(select(User).where(User.email == req.email))
        user = result.scalar_one_or_none()

    if user:
        code = _generate_code()
        code_hash = _hash_code(code)
        expires_at = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=10)

        async with get_async_session_context() as session:
            session.add(
                PasswordResetCode(
                    email=req.email, code_hash=code_hash, expires_at=expires_at
                )
            )
            await session.commit()

        # Send email via existing SMTP utility
        try:
            await send_email(
                "Your Beaver Habits reset code",
                f"Your 12-digit reset code: {code}\nExpires in 10 minutes.",
                [req.email],
            )
            logger.info(f"Reset code sent to {req.email}")
        except Exception as e:
            logger.exception(f"Failed to send reset email to {req.email}: {e}")
            # Don't reveal email existence via error

    return {"message": "If the email exists, a reset code has been sent."}


@router.post("/reset-password")
async def reset_password(req: ResetPasswordRequest, request: Request):
    """Reset password using a 12-digit code.

    Rate limited: 5 requests per minute per IP, 3 per 15 minutes per email.
    """
    client_ip = _get_client_ip(request)

    await _rate_limit_check(f"reset_ip:{client_ip}", limit=5, window=60)
    await _rate_limit_check(f"reset_email:{req.email}", limit=3, window=900)

    # Validate password policy (min 12 chars)
    if len(req.new_password) < 12:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Password must be at least 12 characters",
        )

    code_hash = _hash_code(req.code)

    async with get_async_session_context() as session:
        result = await session.execute(
            select(PasswordResetCode).where(
                PasswordResetCode.email == req.email,
                PasswordResetCode.code_hash == code_hash,
                PasswordResetCode.used == False,
                PasswordResetCode.expires_at > datetime.datetime.now(datetime.timezone.utc),
            )
        )
        reset_code = result.scalar_one_or_none()

        if not reset_code:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid or expired code",
            )

        reset_code.used = True

        # Get user and update password via user_manager (handles hashing)
        user_result = await session.execute(select(User).where(User.email == req.email))
        user = user_result.scalar_one()

        # Update password via user_manager
        user_db = await anext(get_user_db(session))
        user_manager = await anext(get_user_manager(user_db))
        await user_manager._update(user, {"password": req.new_password})

        # TODO: Increment token_version when column exists (Lane 2 #14)
        # user.token_version = (user.token_version or 0) + 1

        await session.commit()

    logger.info(f"Password reset successful for {req.email}")
    return {"message": "Password reset successful. Please log in."}