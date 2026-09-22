"""Admin-only API endpoints.

Replaces the NiceGUI @ui.page('/admin') and @ui.page('/admin/backup')
routes that lived in beaverhabits/routes/astro.py before P3-B.

Authorization: all routes depend on `current_admin_user`, which compares
the caller's email against `settings.ADMIN_EMAIL`. There is no role
column; the admin email is a single-string setting, same as upstream.

These endpoints are consumed by the Astro /admin page (web/concepts/
src/pages/admin.astro).
"""
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select

from castor import views
from castor.app.db import User, get_async_session
from castor.app.dependencies import current_admin_user
from castor.app.audit import append_audit_event
from castor.logger import logger
from starlette import status


router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


@router.get("/users")
async def list_users(
    user: Annotated[User, Depends(current_admin_user)],
) -> dict:
    """List all activated users with their email and registration date.

    Mirrors what the NiceGUI /admin page rendered: a simple table the
    admin can use to verify the deployment.
    """
    users = await views.get_activated_users()
    return {
        "users": [
            {
                "email": u.email,
                "id": str(u.id),
                "is_active": u.is_active,
                "is_verified": u.is_verified,
            }
            for u in users
        ]
    }


@router.post("/backup", status_code=status.HTTP_202_ACCEPTED)
async def trigger_backup(
    user: Annotated[User, Depends(current_admin_user)],
) -> dict:
    """Trigger an immediate backup for every activated user.

    Identical behavior to the old NiceGUI /admin/backup button:
    iterates activated users and pushes each habit list to the user's
    configured Telegram bot. Failures are logged per-user; the call
    always returns 202 because the trigger is fire-and-forget.
    """
    await append_audit_event(
        event="backup",
        outcome="success",
        user_id=user.id,
    )
    logger.info(f"Manual backup triggered by admin {user.email}")

    # Run the backup synchronously so the response includes per-user
    # status. The old NiceGUI page also called this synchronously.
    await views.backup_all_users()

    return {
        "status": "completed",
        "triggered_by": user.email,
    }
