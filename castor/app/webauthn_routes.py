import base64
import asyncio
import hashlib
import json
import re
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from castor.app.audit import record
from castor.app.challenges import ChallengeStore
from castor.app.db import User, WebAuthnCredential, get_async_session_context
from castor.app.rate_limits import consume, retry_after
from castor.logger import logger
from castor.utils import send_email
from typing import Optional
from uuid import UUID
from fastapi import Depends
from pydantic import BaseModel, EmailStr

import webauthn
from webauthn.helpers import bytes_to_base64url, base64url_to_bytes
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    CredentialDeviceType,
    UserVerificationRequirement,
    AttestationConveyancePreference,
    ResidentKeyRequirement,
    PublicKeyCredentialDescriptor,
    PublicKeyCredentialType,
)

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from castor.app.auth import (
    get_async_session_context,
    user_get_by_email,
    user_from_token,
    user_bump_token_version,
)
from castor.app.audit import record
from castor.app.users import get_user_manager, UserManager
from castor.app.dependencies import current_active_user
from castor.configs import settings
from castor.logger import logger


router = APIRouter(prefix="/auth/webauthn", tags=["webauthn"])


challenge_store = ChallengeStore()
BROWSER_COOKIE = "beaver_webauthn"
MAX_CLIENT_DATA_ENCODED = 16384


async def registration_user(http_request: Request) -> User:
    """Only an active session JWT authorizes enrollment, never API tokens."""
    authorization = http_request.headers.get("authorization")
    if authorization:
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer":
            raise HTTPException(401, "Session authentication required")
    else:
        token = http_request.cookies.get("beaver_auth")
    user = await user_from_token(token)
    if not user or not user.is_active:
        raise HTTPException(401, "Session authentication required")
    return user


def _self_binding(username: str, user: User) -> None:
    if username.casefold() != user.email.casefold():
        raise HTTPException(403, "Registration must belong to the signed-in user")


def _browser_cookie(http_request: Request) -> str | None:
    value = http_request.cookies.get(BROWSER_COOKIE, "")
    return value if re.fullmatch(r"[A-Za-z0-9_-]{43}", value) else None


async def _issue_challenge(challenge, ceremony, user, http_request, response):
    browser = _browser_cookie(http_request)
    if browser is None:
        browser = secrets.token_urlsafe(32)
        # A stable browser-session cookie supports concurrent tabs. Do not rotate
        # it per begin: each challenge is indexed independently in the database.
        response.set_cookie(BROWSER_COOKIE, browser, httponly=True,
                            secure=settings.APP_URL.startswith("https://"),
                            samesite="strict", path="/")
    ttl = min(max(settings.WEBAUTHN_TIMEOUT / 1000, 1), 300)
    await challenge_store.issue(challenge, ceremony, user.id, browser, ttl)


def _client_challenge(payload: dict, ceremony: str) -> bytes:
    """Bound parsing only selects the DB record; the verifier authenticates it."""
    try:
        encoded = payload.get("clientDataJSON")
        if not isinstance(encoded, str) or not 0 < len(encoded) <= MAX_CLIENT_DATA_ENCODED:
            raise ValueError()
        if not re.fullmatch(r"[A-Za-z0-9_-]+={0,2}", encoded):
            raise ValueError()
        data = json.loads(base64.b64decode(encoded + "=" * (-len(encoded) % 4),
                                         altchars=b"-_", validate=True))
        if not isinstance(data, dict) or data.get("type") != {"register": "webauthn.create", "login": "webauthn.get"}[ceremony]:
            raise ValueError()
        value = data.get("challenge")
        if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", value):
            raise ValueError()
        challenge = base64url_to_bytes(value)
        if len(challenge) != 32 or bytes_to_base64url(challenge) != value:
            raise ValueError()
        return challenge
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise HTTPException(400, "Invalid client data") from None


async def _consume_challenge(payload, ceremony, user, http_request):
    challenge = _client_challenge(payload, ceremony)
    browser = _browser_cookie(http_request)
    if not browser or not await challenge_store.consume(challenge, ceremony, user.id, browser):
        raise HTTPException(400, "Challenge expired or not found")
    return challenge


class WebAuthnRegisterBeginRequest(BaseModel):
    username: str


class WebAuthnRegisterBeginResponse(BaseModel):
    publicKey: dict


class WebAuthnRegisterCompleteRequest(BaseModel):
    username: str
    id: str
    rawId: str
    type: str
    response: dict
    name: Optional[str] = None


class WebAuthnRegisterCompleteResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class WebAuthnLoginBeginRequest(BaseModel):
    username: str


class WebAuthnLoginBeginResponse(BaseModel):
    publicKey: dict


class WebAuthnLoginCompleteRequest(BaseModel):
    username: str
    id: str
    rawId: str
    type: str
    response: dict


class WebAuthnLoginCompleteResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class VerifyPasswordRequest(BaseModel):
    password: str


def _build_registration_options_dict(options, challenge_b64):
    """Convert webauthn registration options to JSON-serializable dict."""
    return {
        "challenge": challenge_b64,
        "rp": {
            "id": options.rp.id,
            "name": options.rp.name,
        },
        "user": {
            "id": bytes_to_base64url(options.user.id),
            "name": options.user.name,
            "displayName": options.user.display_name,
        },
        "pubKeyCredParams": [
            {"type": param.type, "alg": param.alg}
            for param in options.pub_key_cred_params
        ],
        "timeout": options.timeout,
        "excludeCredentials": [
            {
                "id": bytes_to_base64url(cred.id),
                "type": cred.type,
                "transports": _transport_values(cred.transports),
            }
            for cred in options.exclude_credentials
        ] if options.exclude_credentials else [],
        "authenticatorSelection": {
            "residentKey": options.authenticator_selection.resident_key.value,
            "userVerification": options.authenticator_selection.user_verification.value,
        },
        "attestation": options.attestation.value,
    }


def _transport_values(transports):
    """Extract plain transport strings, tolerating enums or raw strings."""
    return [t.value if hasattr(t, "value") else t for t in transports or []]


def _build_authentication_options_dict(options, challenge_b64):
    """Convert webauthn authentication options to JSON-serializable dict."""
    return {
        "challenge": challenge_b64,
        "rpId": options.rp_id,
        "allowCredentials": [
            {
                "id": bytes_to_base64url(cred.id),
                "type": cred.type,
                "transports": _transport_values(cred.transports),
            }
            for cred in options.allow_credentials
        ],
        "userVerification": options.user_verification.value,
        "timeout": options.timeout,
    }


@router.post("/register/begin", response_model=WebAuthnRegisterBeginResponse)
async def webauthn_register_begin(
    request: WebAuthnRegisterBeginRequest,
    http_request: Request,
    response: Response,
    user_manager: UserManager = Depends(get_user_manager),
    user: User = Depends(registration_user),
):
    """Begin WebAuthn registration ceremony."""
    _self_binding(request.username, user)

    # Get existing credentials for this user
    existing_credentials = await user_manager.get_webauthn_credentials(user)
    exclude_credentials = [
        PublicKeyCredentialDescriptor(
            id=base64url_to_bytes(bytes_to_base64url(cred.id)),
            type=PublicKeyCredentialType.PUBLIC_KEY,
            transports=cred.transports,
        )
        for cred in existing_credentials
    ]

    # Generate registration options
    challenge = secrets.token_bytes(32)
    challenge_b64 = bytes_to_base64url(challenge)

    await _issue_challenge(challenge, "register", user, http_request, response)

    options = webauthn.generate_registration_options(
        rp_id=settings.WEBAUTHN_RP_ID,
        rp_name=settings.WEBAUTHN_RP_NAME,
        user_id=user.id.bytes if hasattr(user.id, 'bytes') else str(user.id).encode(),
        user_name=user.email,
        user_display_name=user.email,
        challenge=challenge,
        timeout=settings.WEBAUTHN_TIMEOUT,
        attestation=AttestationConveyancePreference.NONE,
        exclude_credentials=exclude_credentials,
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.PREFERRED,
            user_verification=UserVerificationRequirement.PREFERRED,
        ),
        supported_pub_key_algs=[-7, -257],  # ES256, RS256
    )

    return WebAuthnRegisterBeginResponse(publicKey=_build_registration_options_dict(options, challenge_b64))


@router.post("/register/complete", response_model=WebAuthnRegisterCompleteResponse)
async def webauthn_register_complete(
    request: WebAuthnRegisterCompleteRequest,
    http_request: Request,
    user_manager: UserManager = Depends(get_user_manager),
    user: User = Depends(registration_user),
):
    """Complete WebAuthn registration ceremony."""
    _self_binding(request.username, user)

    challenge = await _consume_challenge(request.response, "register", user, http_request)

    # Verify registration (py_webauthn parses a raw dict internally)
    try:
        verification = webauthn.verify_registration_response(
            credential={
                "id": request.id,
                "rawId": request.rawId,
                "type": request.type,
                "response": request.response,
            },
            expected_challenge=challenge,
            expected_rp_id=settings.WEBAUTHN_RP_ID,
            expected_origin=settings.WEBAUTHN_ORIGIN,
            require_user_verification=False,  # We use PREFERRED
        )
    except Exception:
        logger.warning("WebAuthn registration verification failed")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Registration verification failed"
        )

    # Store credential (field names per installed py_webauthn 3.x API)
    try:
        aaguid_bytes = UUID(verification.aaguid).bytes if verification.aaguid else None
    except (ValueError, AttributeError):
        aaguid_bytes = None
    existing = await user_manager.get_webauthn_credentials(user)
    cred_name = (request.name or "").strip()[:64]
    if not cred_name:
        cred_name = f"Passkey {len(existing) + 1}"
    cred = await user_manager.add_webauthn_credential(
        user=user,
        credential_id=verification.credential_id,
        public_key=verification.credential_public_key,
        sign_count=verification.sign_count,
        transports=request.response.get("transports") or [],
        aaguid=aaguid_bytes,
        backup_eligible=verification.credential_device_type
        == CredentialDeviceType.MULTI_DEVICE,
        backup_state=verification.credential_backed_up,
        name=cred_name,
    )

    # Generate JWT token
    from castor.app.users import get_jwt_strategy, get_cookie_settings
    strategy = get_jwt_strategy()
    token = await strategy.write_token(user)

    # Registration implies the post-login "set up a passkey?" offer is
    # satisfied (success path). Without this, a user who enrolled via
    # /security would still see the offer on next sign-in.
    from castor.app.db import User as _User, get_async_session_context  # type: ignore
    async with get_async_session_context() as session:
        async with session.begin():
            await session.execute(
                update(_User).where(_User.id == user.id).values(
                    passkey_offer_dismissed=True
                )
            )

    logger.info(f"WebAuthn credential registered for user {user.email}")

    # Set auth cookie with Strict + Secure in production
    cookie_settings = get_cookie_settings()
    response = JSONResponse(
        content={"access_token": token, "token_type": "bearer"}
    )
    response.set_cookie(
        "beaver_auth",
        token,
        **cookie_settings,
        path="/",
        max_age=settings.JWT_LIFETIME_SECONDS or 30 * 24 * 3600,
    )
    return response


@router.post("/check")
async def webauthn_check(
    request: WebAuthnLoginBeginRequest,
    user_manager: UserManager = Depends(get_user_manager),
):
    """Lightweight probe used by the login page for progressive disclosure.

    Returns the same ``OK`` shape regardless of outcome to avoid letting
    an attacker distinguish "email not registered" from "registered but
    no passkey". After login completes the frontend also gates the
    post-login "set up a passkey?" offer on ``passkey_offer_dismissed``;
    having it surfaced here means the BFF can decide the offer with one
    round-trip instead of two.

    Shape::
        {
            "has_passkey": bool,
            "passkey_offer_dismissed": bool,
        }

    All three binary states share the same 200 envelope:

    - has_passkey=true                      → user is registered with passkey
    - has_passkey=false, offer_dismissed=false → user registered, no passkey,
      offer not yet shown
    - has_passkey=false, offer_dismissed=true  → user registered, no passkey,
      offer already shown (or user dismissed it)
    - has_passkey=false, offer_dismissed=true  → unknown email — both fields
      default to false for non-existent accounts

    The endpoint is intentionally cheap (no JWT, no cookie); it is meant
    to be called many times per login.
    """
    user = await user_get_by_email(request.username)
    if not user or not user.is_active:
        return {"has_passkey": False, "passkey_offer_dismissed": False}
    credentials = await user_manager.get_webauthn_credentials(user)
    has_passkey = bool(credentials)
    return {
        "has_passkey": has_passkey,
        # If the user has a passkey, mark the offer as already-dismissed by
        # definition: they enrolled one, so the prompt has been satisfied.
        "passkey_offer_dismissed": bool(
            getattr(user, "passkey_offer_dismissed", False) or has_passkey
        ),
    }


@router.post("/offer/dismiss")
async def dismiss_passkey_offer(
    request: WebAuthnLoginBeginRequest,
):
    """Mark the post-login "Set up a passkey?" offer as dismissed for this user.

    Idempotent. Once dismissed the login page never re-asks the user
    to enrol; enrolment is voluntary via /security.
    """
    async with get_async_session_context() as session:
        async with session.begin():
            result = await session.execute(
                update(User)
                .where(User.email == request.username, User.is_active.is_(True))
                .values(passkey_offer_dismissed=True)
                .execution_options(synchronize_session=False)
            )
            rowcount = getattr(result, "rowcount", 0)
            if rowcount == 0:
                # User unknown / not active — return success shape anyway
                # so this endpoint (like /auth/webauthn/check) does not let
                # an attacker distinguish unknown from known emails.
                return {"ok": False, "detail": "unknown user"}
            return {"ok": True}


@router.post("/login/begin", response_model=WebAuthnLoginBeginResponse)
async def webauthn_login_begin(
    request: WebAuthnLoginBeginRequest,
    http_request: Request,
    response: Response,
    user_manager: UserManager = Depends(get_user_manager),
):
    """Begin WebAuthn authentication ceremony."""
    user = await user_get_by_email(request.username)
    if not user or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )

    # Get existing credentials for this user
    credentials = await user_manager.get_webauthn_credentials(user)
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No passkeys registered for this user"
        )

    # Generate authentication options
    challenge = secrets.token_bytes(32)
    challenge_b64 = bytes_to_base64url(challenge)

    await _issue_challenge(challenge, "login", user, http_request, response)

    allow_credentials = [
        PublicKeyCredentialDescriptor(
            id=base64url_to_bytes(bytes_to_base64url(cred.id)),
            type=PublicKeyCredentialType.PUBLIC_KEY,
            transports=cred.transports,
        )
        for cred in credentials
    ]

    options = webauthn.generate_authentication_options(
        rp_id=settings.WEBAUTHN_RP_ID,
        challenge=challenge,
        timeout=settings.WEBAUTHN_TIMEOUT,
        allow_credentials=allow_credentials,
        user_verification=UserVerificationRequirement.PREFERRED,
    )

    return WebAuthnLoginBeginResponse(publicKey=_build_authentication_options_dict(options, challenge_b64))


@router.post("/login/complete", response_model=WebAuthnLoginCompleteResponse)
async def webauthn_login_complete(
    request: WebAuthnLoginCompleteRequest,
    http_request: Request,
    user_manager: UserManager = Depends(get_user_manager),
):
    """Complete WebAuthn authentication ceremony."""
    user = await user_get_by_email(request.username)
    if not user or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )

    # Get credential from database
    credentials = await user_manager.get_webauthn_credentials(user)
    cred_db = None
    for cred in credentials:
        if bytes_to_base64url(cred.id) == request.id:
            cred_db = cred
            break

    if not cred_db:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Credential not found"
        )

    challenge = await _consume_challenge(request.response, "login", user, http_request)

    # Verify authentication (py_webauthn parses a raw dict internally)
    try:
        verification = webauthn.verify_authentication_response(
            credential={
                "id": request.id,
                "rawId": request.rawId,
                "type": request.type,
                "response": request.response,
            },
            expected_challenge=challenge,
            expected_rp_id=settings.WEBAUTHN_RP_ID,
            expected_origin=settings.WEBAUTHN_ORIGIN,
            credential_public_key=cred_db.public_key,
            credential_current_sign_count=cred_db.sign_count,
            require_user_verification=False,  # We use PREFERRED
        )
    except Exception:
        logger.warning("WebAuthn authentication verification failed")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Authentication verification failed"
        )

    # Compare-and-set on the session that loaded the credential. A concurrent
    # ceremony may have already advanced the counter; never overwrite it stale.
    session = user_manager.user_db.session
    result = await session.execute(update(WebAuthnCredential).where(
        WebAuthnCredential.id == cred_db.id,
        WebAuthnCredential.user_id == user.id,
        WebAuthnCredential.sign_count == cred_db.sign_count,
    ).values(sign_count=verification.new_sign_count))
    if result.rowcount != 1:
        await session.rollback()
        raise HTTPException(400, "Credential changed; please retry")
    await session.commit()

    # Generate JWT token
    from castor.app.users import get_jwt_strategy, get_cookie_settings
    strategy = get_jwt_strategy()
    token = await strategy.write_token(user)

    from castor.app.audit import record
    await record("login", user_id=user.id)

    # Set auth cookie with Strict + Secure in production
    cookie_settings = get_cookie_settings()
    response = JSONResponse(
        content={"access_token": token, "token_type": "bearer"}
    )
    response.set_cookie(
        "beaver_auth",
        token,
        **cookie_settings,
        path="/",
        max_age=settings.JWT_LIFETIME_SECONDS or 30 * 24 * 3600,
    )
    return response


@router.post("/verify-password")
async def webauthn_verify_password(
    request: VerifyPasswordRequest,
    current_user=Depends(current_active_user),
):
    """Step-up auth: confirm the current password before sensitive actions
    (credential delete, password change). The 30-day session alone is not
    enough."""
    from castor.app.auth import user_authenticate

    authed = await user_authenticate(
        email=current_user.email, password=request.password
    )
    if authed is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Current password is incorrect",
        )
    return {"verified": True}


@router.get("/credentials")
async def webauthn_list_credentials(
    user_manager: UserManager = Depends(get_user_manager),
    current_user=Depends(current_active_user),
):
    """List user's registered WebAuthn credentials."""
    credentials = await user_manager.get_webauthn_credentials(current_user)
    return [
        {
            "id": bytes_to_base64url(cred.id),
            "name": cred.name,
            "created_at": cred.created_at.isoformat() if cred.created_at else None,
            "aaguid": bytes_to_base64url(cred.aaguid) if cred.aaguid else None,
            "transports": cred.transports,
            "backup_eligible": cred.backup_eligible,
            "backup_state": cred.backup_state,
        }
        for cred in credentials
    ]


class PasswordChangeRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=4096)
    new_password: str = Field(min_length=1, max_length=4096)


class PasskeyDeleteRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=4096)


@router.post("/change-password")
async def change_account_password(payload: PasswordChangeRequest, user: User = Depends(registration_user)):
    from castor.app.security_actions import change_password, SecurityActionError
    try:
        await change_password(user.id, user.token_version, payload.current_password, payload.new_password)
    except SecurityActionError as exc:
        raise HTTPException(exc.status_code, exc.message) from None
    return {"message": "Password changed. Sign in again."}


@router.delete("/credentials/{credential_id}")
async def webauthn_delete_credential(
    credential_id: str,
    payload: PasskeyDeleteRequest,
    current_user: User = Depends(registration_user),
):
    """Fresh-password gated deletion, shared with the GUI."""
    from castor.app.security_actions import remove_passkey, SecurityActionError
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,1366}", credential_id):
        raise HTTPException(400, "Invalid credential identifier")
    try:
        decoded = base64url_to_bytes(credential_id)
    except Exception:
        raise HTTPException(400, "Invalid credential identifier") from None
    try:
        await remove_passkey(current_user.id, current_user.token_version, payload.current_password, decoded)
    except SecurityActionError as exc:
        raise HTTPException(exc.status_code, exc.message) from None
    return {"success": True}


# ---------------------------------------------------------------------------
# Recovery email — propose + verify a backup address for account recovery.
#
# The flow mirrors password reset: a 6-digit code is mailed, hashed at rest,
# expires in 15 minutes, and is consumed once. Because the user is signed
# in, the action is gated by an active session (not by code-possession).
# A sign-out invalidates in-flight challenges via token_version.
# ---------------------------------------------------------------------------

RECOVERY_CODE_TTL_MINUTES = 15


class RecoveryEmailRequest(BaseModel):
    email: EmailStr


class RecoveryEmailVerifyRequest(BaseModel):
    email: EmailStr
    code: str  # 6 digits, validated client-side
    remove: bool = False  # explicit removal path


def _hash_recovery_code(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


def _generate_recovery_code() -> str:
    return f"{secrets.randbelow(10**6):06d}"


async def _recovery_rate_limit_check(key: str, limit: int, window: int) -> None:
    """Reuses the ``recovery`` namespace from forgot/reset flow."""
    try:
        admitted = await consume("recovery", key, limit, window)
    except Exception:
        raise HTTPException(503, "Rate-limit store unavailable") from None
    if not admitted:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests. Try again later.",
            headers={"Retry-After": retry_after(window)},
        )


def _recovery_email_html(code: str) -> str:
    """Branded email template for the 6-digit recovery-email code."""
    return f"""
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
        <h1 style="margin: 0; font-size: 24px; font-weight: 700; color: #111827;">Castor</h1>
        <p style="margin: 8px 0 0; color: #6b7280;">dam good habits</p>
    </div>

    <div style="background: #f9fafb; border-radius: 12px; padding: 32px; margin: 24px 0;">
        <h2 style="margin: 0 0 16px; font-size: 20px; font-weight: 600; color: #111827;">Confirm your recovery email</h2>
        <p style="margin: 0 0 24px; color: #4b5563;">You are adding a backup email we can use to help you recover your account. Use this 6-digit code:</p>

        <div style="background: #111827; color: #f9fafb; font-family: 'SF Mono', Monaco, 'Cascadia Code', monospace; font-size: 32px; font-weight: 700; letter-spacing: 6px; text-align: center; padding: 20px; border-radius: 8px; margin: 24px 0; user-select: all;">
            {code}
        </div>

        <p style="margin: 24px 0 0; font-size: 14px; color: #9ca3af;">This code expires in <strong>15 minutes</strong>.</p>
        <p style="margin: 16px 0 0; font-size: 14px; color: #9ca3af;">If you didn't request this, you can safely ignore this email.</p>
    </div>

    <hr style="border: none; border-top: 1px solid #e5e7eb; margin: 32px 0;">

    <p style="margin: 0; font-size: 12px; color: #9ca3af; text-align: center;">
        &copy; 2025 Castor. All rights reserved.
    </p>
</body>
</html>
"""


@router.post("/recovery-email")
async def set_recovery_email(
    payload: RecoveryEmailRequest,
    request: Request,
    user: User = Depends(registration_user),
):
    """Step 1 of recovery-email setup. Mails a 6-digit code to ``payload.email``
    and persists a hashed challenge. Identical emails sent twice are idempotent —
    the new code replaces the old.

    Returns ``{ pending: true, email, expires_at }``.
    """
    # Cap per-user churn: at most one in-flight code at a time, but allow
    # re-requesting after expiry. The (user, email, ip) triple shares one bucket
    # so a flood cannot consume a different user's budget.
    client_ip = request.client.host if request.client else "unknown"
    await _recovery_rate_limit_check(
        f"recovery_request_ip:{client_ip}", limit=5, window=300
    )
    await _recovery_rate_limit_check(
        f"recovery_request_user:{user.id}", limit=5, window=300
    )

    proposed = payload.email.strip().lower()
    code = _generate_recovery_code()
    # SQLite strips tzinfo; store naive UTC and compare naive.
    expires_at = (
        datetime.now(timezone.utc).replace(tzinfo=None)
        + timedelta(minutes=RECOVERY_CODE_TTL_MINUTES)
    )

    from castor.app.db import RecoveryEmailChallenge as _Challenge

    async with get_async_session_context() as session:
        async with session.begin():
            # Insert a fresh row. Existing unused rows for the same user are
            # tombstoned by ``used=True`` so a fresh code wins; replay against
            # an old code fails the eligibility filter below.
            await session.execute(
                update(_Challenge)
                .where(
                    _Challenge.user_id == user.id,
                    _Challenge.used.is_(False),
                )
                .values(used=True)
                .execution_options(synchronize_session=False)
            )
            session.add(
                _Challenge(
                    email=proposed,
                    code_hash=_hash_recovery_code(code),
                    expires_at=expires_at,
                    user_id=user.id,
                    token_version=user.token_version,
                )
            )

    try:
        await asyncio.to_thread(
            send_email,
            "Your Castor recovery email code",
            f"Your 6-digit recovery email code: {code}\nExpires in 15 minutes.",
            [proposed],
            html_body=_recovery_email_html(code),
        )
    except Exception:
        logger.warning("Recovery email delivery failed")

    return {
        "pending": True,
        "email": proposed,
        "expires_at": expires_at.isoformat(),
    }


@router.post("/recovery-email/verify")
async def verify_recovery_email(
    payload: RecoveryEmailVerifyRequest,
    user: User = Depends(registration_user),
):
    """Step 2 of recovery-email setup. The user pastes the 6-digit code.

    On success, atomically:
      - selects the unused, unexpired, matching challenge row,
      - flips ``user.recovery_email`` + ``recovery_email_verified``,
      - marks the challenge ``used=True``.

    On ``remove=True``, the user is clearing an existing recovery email
    — no code required (current session is the proof of intent).
    """
    if payload.remove:
        if not user.recovery_email:
            # Already gone — idempotent removal.
            return {"recovery_email": None, "recovery_email_verified": False}
        # Rate-limit destructive actions
        await _recovery_rate_limit_check(
            f"recovery_remove_user:{user.id}", limit=5, window=300
        )
        from castor.app.db import RecoveryEmailChallenge as _Challenge
        async with get_async_session_context() as session:
            async with session.begin():
                await session.execute(
                    update(User).where(User.id == user.id).values(
                        recovery_email=None, recovery_email_verified=False
                    )
                )
                # Tombstone any in-flight challenges for this user
                await session.execute(
                    update(_Challenge)
                    .where(
                        _Challenge.user_id == user.id,
                        _Challenge.used.is_(False),
                    )
                    .values(used=True)
                    .execution_options(synchronize_session=False)
                )
        await record("recovery_email_removed", user_id=user.id)
        return {"recovery_email": None, "recovery_email_verified": False}

    # Standard verify path
    if not re.fullmatch(r"\d{6}", payload.code):
        raise HTTPException(400, "Code must be exactly 6 digits")
    proposed = payload.email.strip().lower()

    from castor.app.db import RecoveryEmailChallenge as _Challenge
    code_hash = _hash_recovery_code(payload.code)
    # SQLite strips tzinfo; compare in naive UTC.
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    async with get_async_session_context() as session:
        async with session.begin():
            candidate = (
                await session.execute(
                    select(_Challenge)
                    .where(
                        _Challenge.email == proposed,
                        _Challenge.code_hash == code_hash,
                        _Challenge.used.is_(False),
                        _Challenge.expires_at > now,
                        _Challenge.user_id == user.id,
                        _Challenge.token_version == user.token_version,
                    )
                    .with_for_update()
                    .limit(1)
                )
            ).scalar_one_or_none()
            if candidate is None:
                raise HTTPException(400, "Invalid or expired code")

            previous = getattr(user, "recovery_email", None)
            previously_verified = bool(
                getattr(user, "recovery_email_verified", False)
            )
            await session.execute(
                update(User).where(User.id == user.id).values(
                    recovery_email=proposed,
                    recovery_email_verified=True,
                )
            )
            await session.execute(
                update(_Challenge)
                .where(_Challenge.id == candidate.id)
                .values(used=True)
            )

    if previous and previous.casefold() != proposed.casefold():
        await record("recovery_email_replaced", user_id=user.id)
    else:
        await record("recovery_email_set", user_id=user.id)
    return {"recovery_email": proposed, "recovery_email_verified": True}


# ---------------------------------------------------------------------------
# /auth/logout — bump token_version so any cached JWT for this account is
# invalidated. Cookie clearing happens on the Astro side.
#
# NOTE: fastapi_users' get_auth_router also provides /auth/logout (which
# returns the bearer transport's logout payload). This endpoint is mounted
# in app/app.py AFTER the auth router so it shadows the fastapi-users one
# at the same path.
# ---------------------------------------------------------------------------


logout_router = APIRouter(prefix="/auth", tags=["auth"])


@logout_router.post("/logout", status_code=204)
async def auth_logout(user: User = Depends(current_active_user)) -> Response:
    """Invalidate every outstanding token for this user.

    The Astro client clears the httpOnly cookie on receipt of a 2xx; this
    endpoint additionally bumps token_version so even a stolen JWT copied
    before logout is rejected at the next request (VersionedJWTStrategy
    compares the JWT's 'ver' claim against the user's current version).

    Mounted in app.py BEFORE fastapi_users.get_auth_router so it wins
    the route-match for POST /auth/logout (FastAPI iterates routes in
    registration order; first match wins).
    """
    await user_bump_token_version(user)
    await record("logout", user_id=user.id)
    return Response(status_code=204)
