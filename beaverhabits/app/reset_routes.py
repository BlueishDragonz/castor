import datetime
import hashlib
import secrets
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi_users.exceptions import InvalidPasswordException
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
    logger.info(f"forgot_password called for email={req.email}, ip={client_ip}")

    # Rate limit by IP
    await _rate_limit_check(f"forgot_ip:{client_ip}", limit=3, window=60)
    # Rate limit by email (prevents hammering a specific address)
    await _rate_limit_check(f"forgot_email:{req.email}", limit=1, window=900)

    # Check if user exists (but don't reveal)
    async with get_async_session_context() as session:
        result = await session.execute(select(User).where(User.email == req.email))
        user = result.scalar_one_or_none()

    logger.info(f"User exists: {user is not None}")

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
            code_html = f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
</head>
<body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; line-height: 1.6; color: #1f2937; max-width: 600px; margin: 0 auto; padding: 20px;">
    <div style="text-align: center; padding: 30px 0;">
        <div style="display: inline-block; width: 64px; height: 64px; background: linear-gradient(135deg, #3b82f6, #8b5cf6); border-radius: 16px; margin-bottom: 16px;">
            <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="white" stroke-width="2" style="margin: 16px;">
                <path d="M12 2L2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5"/>
            </svg>
        </div>
        <h1 style="margin: 0; font-size: 24px; font-weight: 700; color: #111827;">Beaver Habits</h1>
        <p style="margin: 8px 0 0; color: #6b7280;">dam good habits</p>
    </div>
    
    <div style="background: #f9fafb; border-radius: 12px; padding: 32px; margin: 24px 0;">
        <h2 style="margin: 0 0 16px; font-size: 20px; font-weight: 600; color: #111827;">Reset your password</h2>
        <p style="margin: 0 0 24px; color: #4b5563;">You requested a password reset. Use the 12-digit code below:</p>
        
        <div style="background: #111827; color: #f9fafb; font-family: 'SF Mono', Monaco, 'Cascadia Code', monospace; font-size: 28px; font-weight: 700; letter-spacing: 4px; text-align: center; padding: 20px; border-radius: 8px; margin: 24px 0; user-select: all;">
            {code}
        </div>
        
        <p style="margin: 24px 0 0; font-size: 14px; color: #9ca3af;">This code expires in <strong>10 minutes</strong>.</p>
        <p style="margin: 16px 0 0; font-size: 14px; color: #9ca3af;">If you didn't request this, you can safely ignore this email.</p>
    </div>
    
    <hr style="border: none; border-top: 1px solid #e5e7eb; margin: 32px 0;">
    
    <p style="margin: 0; font-size: 12px; color: #9ca3af; text-align: center;">
        © 2025 Beaver Habits. All rights reserved.
    </p>
</body>
</html>
"""
            await send_email(
                "Your Beaver Habits reset code",
                f"Your 12-digit reset code: {code}\nExpires in 10 minutes.",
                [req.email],
                html_body=code_html,
            )
            logger.info(f"Reset code sent to {req.email}")
        except Exception as e:
            logger.exception(f"Failed to send reset email to {req.email}: {e}")
            # Don't reveal email existence via error

    response = {"message": "If the email exists, a reset code has been sent."}
    logger.info(f"Returning response: {response}")
    return response


@router.post("/reset-password")
async def reset_password(req: ResetPasswordRequest, request: Request):
    """Reset password using a 12-digit code.

    Rate limited: 5 requests per minute per IP, 3 per 15 minutes per email.
    """
    client_ip = _get_client_ip(request)

    await _rate_limit_check(f"reset_ip:{client_ip}", limit=5, window=60)
    await _rate_limit_check(f"reset_email:{req.email}", limit=3, window=900)

    # Shared policy: reject before consuming a reset code.
    try:
        await UserManager(None).validate_password(req.new_password, None)
    except InvalidPasswordException as exc:
        raise HTTPException(status_code=400, detail=exc.reason) from exc

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


        await session.commit()

    logger.info(f"Password reset successful for {req.email}")
    return {"message": "Password reset successful. Please log in."}