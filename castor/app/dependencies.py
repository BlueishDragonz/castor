from typing import Annotated, Optional

from fastapi import Depends, HTTPException, Request
from fastapi.security.utils import get_authorization_scheme_param
from starlette.status import HTTP_401_UNAUTHORIZED, HTTP_503_SERVICE_UNAVAILABLE

from castor import views
from castor.app.auth import (
    user_from_reset_token,
    user_from_token,
    user_get_by_email,
)
from castor.app.crud import get_user_by_api_token
from castor.app.db import User
from castor.configs import settings
from castor.logger import logger


def get_bearer_token(request: Request) -> Optional[str]:
    authorization = request.headers.get("Authorization")
    if not authorization:
        return None

    scheme, param = get_authorization_scheme_param(authorization)
    if scheme.lower() != "bearer":
        return None

    return param


def get_trusted_header_email(request: Request) -> Optional[str]:
    if not settings.TRUSTED_EMAIL_HEADER:
        return None

    return request.headers.get(settings.TRUSTED_EMAIL_HEADER)


def get_trusted_local_email() -> Optional[str]:
    return settings.TRUSTED_LOCAL_EMAIL


async def current_active_user(
    credentials: Annotated[Optional[str], Depends(get_bearer_token)],
    trusted_header_email: Annotated[Optional[str], Depends(get_trusted_header_email)],
    trusted_local_email: Annotated[Optional[str], Depends(get_trusted_local_email)],
) -> User:
    if trusted_header_email:
        if user := await user_get_by_email(trusted_header_email):
            return user
        else:
            raise HTTPException(
                status_code=HTTP_401_UNAUTHORIZED,
                detail=f"Trusted email user not found: {trusted_header_email}",
            )

    if trusted_local_email:
        logger.info(f"Trusted local email: {trusted_local_email}")
        if user := await user_get_by_email(trusted_local_email):
            return user
        logger.info(f"Trusted local email user not found. Creating user.")
        user = await views.register_user(trusted_local_email)
        return user

    if credentials and (user := await user_from_token(credentials)):
        return user

    # Check API token (permanent tokens for integrations)
    if credentials and (user := await get_user_by_api_token(credentials)):
        return user

    # ref: fastapi.security.oauth2.OAuth2PasswordBearer
    raise HTTPException(
        status_code=HTTP_401_UNAUTHORIZED,
        detail="Not authenticated",
        headers={"WWW-Authenticate": "Bearer"},
    )


async def current_admin_user(
    user: Annotated[User, Depends(current_active_user)],
) -> User:
    # F16: this compared `user.email != settings.ADMIN_EMAIL`. With
    # ADMIN_EMAIL unset that is `user.email != ""`, which is True for every
    # real account — so the admin surface was a permanent 401 with no way to
    # recover except editing the environment and restarting. A latent lockout
    # wearing the costume of a security control.
    #
    # It is now fail-CLOSED with an honest signal: an unset ADMIN_EMAIL means
    # "no administrator is configured", which is a deployment error, not an
    # authentication decision. It is logged loudly and the request is refused
    # with 503 (not 401), because a 401 tells the operator to fix their
    # credentials when the real problem is missing configuration.
    #
    # Comparing case-insensitively also avoids an admin being locked out by
    # case differences between the env var and their address, which is exactly
    # the kind of silent failure this is guarding against.
    admin_email = (settings.ADMIN_EMAIL or "").strip()
    if not admin_email:
        logger.error(
            "ADMIN_EMAIL is not configured, so no user can ever be an admin. "
            "Set ADMIN_EMAIL to the address that should have admin access."
        )
        raise HTTPException(
            status_code=HTTP_503_SERVICE_UNAVAILABLE,
            detail="Admin access is not configured on this server.",
        )

    if user.email.strip().lower() != admin_email.lower():
        logger.warning(
            f"User {user.email} tried to access admin endpoint without admin privileges."
        )
        raise HTTPException(
            status_code=HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


async def get_reset_user(request: Request) -> User:
    token = request.query_params.get("token")
    if not token:
        raise HTTPException(
            status_code=HTTP_401_UNAUTHORIZED,
            detail="Token not found",
        )

    from fastapi_users import exceptions
    try:
        return await user_from_reset_token(token)
    except (exceptions.InvalidResetPasswordToken, exceptions.UserInactive, exceptions.UserNotExists):
        raise HTTPException(303, detail="Reset link invalid or expired",
                            headers={"Location": "/login?recovery=expired"}) from None
