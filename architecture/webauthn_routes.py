import base64
import json
import re
import secrets

from sqlalchemy import update
from beaverhabits.app.challenges import ChallengeStore
from beaverhabits.app.db import User, WebAuthnCredential
from typing import Optional
from uuid import UUID

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

from beaverhabits.app.auth import user_get_by_email, user_from_token
from beaverhabits.app.users import get_user_manager, UserManager
from beaverhabits.app.dependencies import current_active_user
from beaverhabits.configs import settings
from beaverhabits.logger import logger


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
    from beaverhabits.app.users import get_jwt_strategy, get_cookie_settings
    strategy = get_jwt_strategy()
    token = await strategy.write_token(user)

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
    from beaverhabits.app.users import get_jwt_strategy, get_cookie_settings
    strategy = get_jwt_strategy()
    token = await strategy.write_token(user)

    from beaverhabits.app.audit import record
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
    from beaverhabits.app.auth import user_authenticate

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
    from beaverhabits.app.security_actions import change_password, SecurityActionError
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
    from beaverhabits.app.security_actions import remove_passkey, SecurityActionError
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
