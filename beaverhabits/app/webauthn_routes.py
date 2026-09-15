import base64
import json
import secrets
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

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from beaverhabits.app.auth import user_get_by_email
from beaverhabits.app.users import get_user_manager, UserManager
from beaverhabits.app.dependencies import current_active_user
from beaverhabits.configs import settings
from beaverhabits.logger import logger


router = APIRouter(prefix="/auth/webauthn", tags=["webauthn"])


# In-memory challenge storage (in production, use Redis or database)
challenge_store = {}


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
    user_manager: UserManager = Depends(get_user_manager),
):
    """Begin WebAuthn registration ceremony."""
    user = await user_get_by_email(request.username)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )

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

    # Store challenge for verification
    challenge_store[f"register:{user.id}"] = challenge_b64

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
    user_manager: UserManager = Depends(get_user_manager),
):
    """Complete WebAuthn registration ceremony."""
    user = await user_get_by_email(request.username)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )

    # Retrieve challenge
    challenge_key = f"register:{user.id}"
    challenge_b64 = challenge_store.pop(challenge_key, None)
    if not challenge_b64:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Challenge expired or not found"
        )

    challenge = base64url_to_bytes(challenge_b64)

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
    except Exception as e:
        logger.exception("WebAuthn registration verification failed")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Registration verification failed: {str(e)}"
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
    user_manager: UserManager = Depends(get_user_manager),
):
    """Begin WebAuthn authentication ceremony."""
    user = await user_get_by_email(request.username)
    if not user:
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

    # Store challenge for verification
    challenge_store[f"login:{user.id}"] = challenge_b64

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
    user_manager: UserManager = Depends(get_user_manager),
):
    """Complete WebAuthn authentication ceremony."""
    user = await user_get_by_email(request.username)
    if not user:
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

    # Retrieve challenge
    challenge_key = f"login:{user.id}"
    challenge_b64 = challenge_store.pop(challenge_key, None)
    if not challenge_b64:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Challenge expired or not found"
        )

    challenge = base64url_to_bytes(challenge_b64)

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
    except Exception as e:
        logger.exception("WebAuthn authentication verification failed")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Authentication verification failed: {str(e)}"
        )

    # Update sign count
    from beaverhabits.app.auth import get_async_session_context
    async with get_async_session_context() as session:
        cred_db.sign_count = verification.new_sign_count
        await session.commit()

    # Generate JWT token
    from beaverhabits.app.users import get_jwt_strategy, get_cookie_settings
    strategy = get_jwt_strategy()
    token = await strategy.write_token(user)

    logger.info(f"WebAuthn login successful for user {user.email}")

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


@router.delete("/credentials/{credential_id}")
async def webauthn_delete_credential(
    credential_id: str,
    user_manager: UserManager = Depends(get_user_manager),
    current_user=Depends(current_active_user),
):
    """Delete a WebAuthn credential. Refuses to remove the last sign-in
    method (anti-stranding: no passkeys left and no password set)."""
    credentials = await user_manager.get_webauthn_credentials(current_user)
    cred_id_bytes = base64url_to_bytes(credential_id)
    remaining = [c for c in credentials if bytes(c.id) != cred_id_bytes]
    if not remaining and not getattr(current_user, "hashed_password", None):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cannot remove your last sign-in method: set a password first",
        )
    deleted = await user_manager.delete_webauthn_credential(current_user, cred_id_bytes)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Credential not found"
        )
    return {"success": True}