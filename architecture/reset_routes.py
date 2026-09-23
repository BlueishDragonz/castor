import asyncio
import datetime
import hashlib
import secrets
from fastapi import APIRouter, HTTPException, Request, status
from fastapi_users.exceptions import InvalidPasswordException
from pydantic import BaseModel, EmailStr
from sqlalchemy import select, update

from beaverhabits.app.db import (
    PasswordResetCode,
    User,
    get_async_session_context,
)
from beaverhabits.app.users import UserManager
from beaverhabits.app.rate_limits import consume, retry_after
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


# Kept only for older isolated fixtures that clear it; admission never reads it.
_rate_limit_cache: dict = {}


async def _rate_limit_check(key: str, limit: int, window: int) -> None:
    """Preserve recovery-specific budgets using the existing persistent store.

    The outer IPRateLimitMiddleware already enforces the overall auth-IP budget.
    This replaces, rather than layers onto, the old process-local limiter.
    """
    try:
        allowed = await consume("recovery", key, limit, window)
    except Exception:
        raise HTTPException(503, "Rate-limit store unavailable") from None
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests. Try again later.",
            headers={"Retry-After": retry_after(window)},
        )


def _get_client_ip(request: Request) -> str:
    # Proxy trust belongs to the ASGI server, not client-supplied headers here.
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

    if user and user.is_active:
        code = _generate_code()
        code_hash = _hash_code(code)
        expires_at = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=10)

        async with get_async_session_context() as session:
            session.add(
                PasswordResetCode(
                    email=req.email, code_hash=code_hash, expires_at=expires_at,
                    user_id=user.id, token_version=user.token_version,
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
            await asyncio.to_thread(
                send_email,
                "Your Beaver Habits reset code",
                f"Your 12-digit reset code: {code}\nExpires in 10 minutes.",
                [req.email],
                html_body=code_html,
            )
        except Exception:
            # SMTP exceptions may contain recipients, credentials or message bodies.
            logger.warning("Recovery email delivery failed")
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

    # Shared policy: reject before hashing or consuming a reset code.
    manager = UserManager(None)
    try:
        await manager.validate_password(req.new_password, None)
    except InvalidPasswordException as exc:
        raise HTTPException(status_code=400, detail=exc.reason) from exc

    def invalid_code():
        return HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid or expired code")

    code_hash = _hash_code(req.code)

    def eligible_code():
        return (
            PasswordResetCode.email == req.email,
            PasswordResetCode.code_hash == code_hash,
            PasswordResetCode.used.is_(False),
            PasswordResetCode.expires_at > datetime.datetime.now(datetime.timezone.utc),
            PasswordResetCode.user_id.is_not(None),
            PasswordResetCode.token_version.is_not(None),
        )

    def candidate_query():
        # Selection and consumption share ONE transaction; with_for_update()
        # locks the code row from selection to commit on PostgreSQL (a no-op on
        # SQLite, which serializes writers anyway). Hashing happens BEFORE this
        # transaction so no lock is held during slow password work.
        return (
            select(PasswordResetCode.id, PasswordResetCode.user_id, PasswordResetCode.token_version)
            .join(User, User.id == PasswordResetCode.user_id)
            .where(*eligible_code(), User.email == req.email, User.is_active.is_(True),
                   User.token_version == PasswordResetCode.token_version)
            .order_by(PasswordResetCode.id).limit(1)
            .with_for_update()
        )

    hashed_password = await asyncio.to_thread(manager.password_helper.hash, req.new_password)
    async with get_async_session_context() as session:
        async with session.begin():
            candidate = (await session.execute(candidate_query())).first()
            if candidate is None:
                raise invalid_code()
            # Compare-and-swap the account first: sibling codes share an issuance
            # version, so only one concurrent reset can change this account.
            changed = await session.execute(
                update(User).where(
                    User.id == candidate.user_id,
                    User.email == req.email,
                    User.is_active.is_(True),
                    User.token_version == candidate.token_version,
                ).values(hashed_password=hashed_password, token_version=User.token_version + 1)
                .execution_options(synchronize_session=False)
            )
            if changed.rowcount != 1:
                raise invalid_code()
            consumed = await session.execute(
                update(PasswordResetCode).where(
                    PasswordResetCode.id == candidate.id,
                    PasswordResetCode.user_id == candidate.user_id,
                    PasswordResetCode.token_version == candidate.token_version,
                    *eligible_code(),
                ).values(used=True).execution_options(synchronize_session=False)
            )
            if consumed.rowcount != 1:
                # Raising rolls back the password/version too, including expiry
                # during hashing and races with another consumer.
                raise invalid_code()
            # The context commits ONCE. SQLAlchemyUserDatabase.update must not be
            # used here: it commits independently, splitting the security boundary.

    from beaverhabits.app.audit import record
    await record("password_reset", user_id=candidate.user_id)
    return {"message": "Password reset successful. Please log in."}