"""Private Circle HTTP API (Phase 3 P2).

Mounted by routes/api.py at /api/v1/circles. All routes require an
authenticated user (current_active_user). Membership and ownership
checks are enforced on every endpoint — never trust the path params
for authorization.

Schema lives in app/circles.py. This module is the thin handler layer.

Endpoint summary:
  GET    /                                  list circles I belong to / own
  POST   /                                  create circle (caller becomes owner)
  GET    /{circle_id}                       detail: members + shared habits
  DELETE /{circle_id}                       owner only: delete circle
  POST   /{circle_id}/members               owner only: add member by email
  DELETE /{circle_id}/members/{user_id}     owner removes; self can also leave
  POST   /{circle_id}/habits                owner only: share {habit_id, visibility}
  DELETE /{circle_id}/habits/{habit_id}     owner only: un-share
  POST   /{circle_id}/invites               owner only: mint invite (token shown ONCE)
  GET    /{circle_id}/invites               owner only: list pending invites
  DELETE /{circle_id}/invites/{invite_id}   owner only: revoke
  POST   /{circle_id}/join                  any user with a valid token: accept invite
  GET    /{circle_id}/feed                  members: per-habit today/yesterday streak + notes
"""
import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from loguru import logger
from pydantic import BaseModel, Field

from castor import views
from castor.app import circles as circles_mod
from castor.app.audit import record
from castor.app.dependencies import current_active_user
from castor.app.db import User, async_session_maker
from castor.configs import settings
from castor.core.completions import get_habit_date_completion
from castor.storage.storage import HabitListNotFoundError

from sqlalchemy import delete, select


router = APIRouter(prefix="/circles", tags=["circles"])


# ----------------------------------------------------------------------
# Pydantic schemas — match the wire shape the Astro front-end expects.
# ----------------------------------------------------------------------


class CircleCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)


class CircleRead(BaseModel):
    id: int
    name: str
    owner_id: str
    is_owner: bool
    member_count: int
    shared_habit_count: int
    created_at: datetime.datetime


class CircleMemberRead(BaseModel):
    user_id: str
    email: str
    is_owner: bool
    joined_at: datetime.datetime


class CircleHabitShare(BaseModel):
    habit_id: str
    visibility: Literal["ticks", "ticks+streak", "ticks+streak+notes"] = "ticks"


class CircleHabitRead(BaseModel):
    habit_id: str
    owner_email: str
    name: str
    visibility: str
    share_notes: bool
    streak: int  # current streak, computed
    # Per-day records for the visible window. Each entry: {day, done, text?}
    records: list[dict]


class CircleInviteCreate(BaseModel):
    delivery: Literal["link", "email"] = "link"
    invited_email: str | None = None  # required when delivery='email'
    ttl_hours: int = Field(default=72, ge=1, le=720)  # 1 hour .. 30 days


class CircleInviteRead(BaseModel):
    id: int
    delivery: str
    invited_email: str | None
    expires_at: datetime.datetime
    used_at: datetime.datetime | None
    # Raw token is only populated for the response that immediately follows
    # the create — never re-fetchable.
    raw_token: str | None = None


class CircleJoin(BaseModel):
    token: str


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


async def _load_circle(session, circle_id: int) -> circles_mod.Circle:
    circle = (await session.execute(
        select(circles_mod.Circle).where(circles_mod.Circle.id == circle_id)
    )).scalar_one_or_none()
    if circle is None:
        raise HTTPException(status_code=404, detail="Circle not found")
    return circle


async def _require_owner_or_member(circle_id: int, user: User) -> circles_mod.Circle:
    async with async_session_maker() as session:
        circle = await _load_circle(session, circle_id)
        if str(user.id) == str(circle.owner_id):
            return circle
        is_member = (await session.execute(
            select(circles_mod.CircleMember).where(
                circles_mod.CircleMember.circle_id == circle_id,
                circles_mod.CircleMember.user_id == str(user.id),
            )
        )).scalar_one_or_none()
        if is_member is None:
            raise HTTPException(status_code=403, detail="Not a member of this circle")
        return circle


async def _require_owner(circle_id: int, user: User) -> circles_mod.Circle:
    async with async_session_maker() as session:
        circle = await _load_circle(session, circle_id)
        if str(user.id) != str(circle.owner_id):
            raise HTTPException(status_code=403, detail="Owner only")
        return circle


# ----------------------------------------------------------------------
# Routes
# ----------------------------------------------------------------------


@router.get("", response_model=list[CircleRead])
async def list_my_circles(user: User = Depends(current_active_user)):
    async with async_session_maker() as session:
        rows = (await session.execute(
            select(circles_mod.Circle)
            .outerjoin(
                circles_mod.CircleMember,
                circles_mod.CircleMember.circle_id == circles_mod.Circle.id,
            )
            .where(
                (circles_mod.Circle.owner_id == str(user.id))
                | (circles_mod.CircleMember.user_id == str(user.id))
            )
            .distinct()
            .order_by(circles_mod.Circle.created_at.desc())
        )).scalars().all()

        out: list[CircleRead] = []
        for c in rows:
            member_count = (await session.execute(
                select(circles_mod.CircleMember).where(
                    circles_mod.CircleMember.circle_id == c.id
                )
            )).scalars().all()
            shared_count = (await session.execute(
                select(circles_mod.CircleHabit).where(
                    circles_mod.CircleHabit.circle_id == c.id
                )
            )).scalars().all()
            out.append(CircleRead(
                id=c.id,
                name=c.name,
                owner_id=str(c.owner_id),
                is_owner=str(user.id) == str(c.owner_id),
                member_count=len(member_count),
                shared_habit_count=len(shared_count),
                created_at=c.created_at,
            ))
        return out


@router.post("", response_model=CircleRead, status_code=201)
async def create_circle(
    body: CircleCreate,
    user: User = Depends(current_active_user),
):
    async with async_session_maker() as session:
        async with session.begin():
            circle = circles_mod.Circle(name=body.name, owner_id=user.id)
            session.add(circle)
            await session.flush()
            # Owner is automatically a member so they see their own shared habits.
            session.add(circles_mod.CircleMember(circle_id=circle.id, user_id=user.id))
            await session.flush()
            circle_id = circle.id
            created_at = circle.created_at
        await record("circle_create", user_id=user.id)
        return CircleRead(
            id=circle_id,
            name=body.name,
            owner_id=str(user.id),
            is_owner=True,
            member_count=1,
            shared_habit_count=0,
            created_at=created_at,
        )


@router.get("/{circle_id}", response_model=dict)
async def get_circle(
    circle_id: int,
    user: User = Depends(current_active_user),
):
    circle = await _require_owner_or_member(circle_id, user)
    async with async_session_maker() as session:
        members = (await session.execute(
            select(circles_mod.CircleMember, User)
            .join(User, circles_mod.CircleMember.user_id == User.id)
            .where(circles_mod.CircleMember.circle_id == circle_id)
            .order_by(circles_mod.CircleMember.created_at)
        )).all()
        shared = (await session.execute(
            select(circles_mod.CircleHabit)
            .where(circles_mod.CircleHabit.circle_id == circle_id)
            .order_by(circles_mod.CircleHabit.created_at)
        )).scalars().all()
        owner = (await session.execute(
            select(User).where(User.id == circle.owner_id)
        )).scalar_one()

    return {
        "id": circle.id,
        "name": circle.name,
        "owner_id": str(circle.owner_id),
        "owner_email": owner.email,
        "is_owner": str(user.id) == str(circle.owner_id),
        "members": [
            CircleMemberRead(
                user_id=str(m.user_id),
                email=u.email,
                is_owner=str(u.id) == str(circle.owner_id),
                joined_at=m.created_at,
            )
            for (m, u) in members
        ],
        "shared_habits": [
            {"habit_id": h.habit_id, "visibility": h.visibility, "share_notes": h.share_notes}
            for h in shared
        ],
        "created_at": circle.created_at,
    }


@router.delete("/{circle_id}", status_code=204)
async def delete_circle(
    circle_id: int,
    user: User = Depends(current_active_user),
):
    await _require_owner(circle_id, user)
    async with async_session_maker() as session:
        async with session.begin():
            await session.execute(
                delete(circles_mod.Circle).where(circles_mod.Circle.id == circle_id)
            )
    await record("circle_delete", user_id=user.id)
    return


class AddMember(BaseModel):
    email: str


@router.post("/{circle_id}/members", status_code=201)
async def add_member(
    circle_id: int,
    body: AddMember,
    user: User = Depends(current_active_user),
):
    """Owner adds an existing user by email. (Open registration is the
    alternative; we don't auto-create accounts here.)"""
    await _require_owner(circle_id, user)
    from castor.app.auth import user_get_by_email
    target = await user_get_by_email(body.email)
    if target is None:
        raise HTTPException(status_code=404, detail="No user with that email")
    async with async_session_maker() as session:
        async with session.begin():
            existing = (await session.execute(
                select(circles_mod.CircleMember).where(
                    circles_mod.CircleMember.circle_id == circle_id,
                    circles_mod.CircleMember.user_id == str(target.id),
                )
            )).scalar_one_or_none()
            if existing is not None:
                raise HTTPException(status_code=409, detail="Already a member")
            session.add(circles_mod.CircleMember(circle_id=circle_id, user_id=target.id))
    await record("circle_join", user_id=user.id)
    return {"user_id": str(target.id), "email": target.email}


@router.delete("/{circle_id}/members/{user_id}", status_code=204)
async def remove_member(
    circle_id: int,
    user_id: str,
    user: User = Depends(current_active_user),
):
    """Owner removes a member, or any member removes themselves."""
    is_self = str(user.id) == user_id
    if not is_self:
        await _require_owner(circle_id, user)
    else:
        await _require_owner_or_member(circle_id, user)
    async with async_session_maker() as session:
        async with session.begin():
            result = await session.execute(
                delete(circles_mod.CircleMember).where(
                    circles_mod.CircleMember.circle_id == circle_id,
                    circles_mod.CircleMember.user_id == user_id,
                )
            )
            if result.rowcount == 0:
                raise HTTPException(status_code=404, detail="Not a member")
    await record("circle_leave", user_id=user.id)
    return


@router.post("/{circle_id}/habits", status_code=201)
async def share_habit(
    circle_id: int,
    body: CircleHabitShare,
    user: User = Depends(current_active_user),
):
    circle = await _require_owner(circle_id, user)
    # Validate the habit belongs to the circle owner.
    try:
        await views.get_user_habit(user, body.habit_id)
    except Exception:
        raise HTTPException(status_code=404, detail="Habit not found")
    share_notes = body.visibility == circles_mod.VIS_TICKS_STREAK_NOTES
    async with async_session_maker() as session:
        async with session.begin():
            existing = (await session.execute(
                select(circles_mod.CircleHabit).where(
                    circles_mod.CircleHabit.circle_id == circle_id,
                    circles_mod.CircleHabit.habit_id == body.habit_id,
                )
            )).scalar_one_or_none()
            if existing is not None:
                existing.visibility = body.visibility
                existing.share_notes = share_notes
            else:
                session.add(circles_mod.CircleHabit(
                    circle_id=circle_id,
                    habit_id=body.habit_id,
                    owner_user_id=user.id,
                    visibility=body.visibility,
                    share_notes=share_notes,
                ))
    await record("circle_habit_share", user_id=user.id)
    return {"habit_id": body.habit_id, "visibility": body.visibility, "share_notes": share_notes}


@router.delete("/{circle_id}/habits/{habit_id}", status_code=204)
async def unshare_habit(
    circle_id: int,
    habit_id: str,
    user: User = Depends(current_active_user),
):
    await _require_owner(circle_id, user)
    async with async_session_maker() as session:
        async with session.begin():
            result = await session.execute(
                delete(circles_mod.CircleHabit).where(
                    circles_mod.CircleHabit.circle_id == circle_id,
                    circles_mod.CircleHabit.habit_id == habit_id,
                )
            )
            if result.rowcount == 0:
                raise HTTPException(status_code=404, detail="Habit not shared in this circle")
    await record("circle_habit_unshare", user_id=user.id)
    return


@router.post("/{circle_id}/invites", response_model=CircleInviteRead, status_code=201)
async def create_invite(
    circle_id: int,
    body: CircleInviteCreate,
    user: User = Depends(current_active_user),
):
    await _require_owner(circle_id, user)
    if body.delivery == "email" and not body.invited_email:
        raise HTTPException(status_code=400, detail="invited_email required for email delivery")
    raw, hashed = circles_mod.mint_invite_token()
    expires = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=body.ttl_hours)
    async with async_session_maker() as session:
        async with session.begin():
            invite = circles_mod.CircleInvite(
                circle_id=circle_id,
                token_hash=hashed,
                delivery=body.delivery,
                invited_email=body.invited_email,
                expires_at=expires,
                invited_by=user.id,
            )
            session.add(invite)
            await session.flush()
            invite_id = invite.id
            created_at = invite.created_at
    await record("circle_invite_create", user_id=user.id)
    return CircleInviteRead(
        id=invite_id,
        delivery=body.delivery,
        invited_email=body.invited_email,
        expires_at=expires,
        used_at=None,
        raw_token=raw,  # shown ONCE; never stored
    )


@router.get("/{circle_id}/invites", response_model=list[CircleInviteRead])
async def list_invites(
    circle_id: int,
    user: User = Depends(current_active_user),
):
    await _require_owner(circle_id, user)
    async with async_session_maker() as session:
        rows = (await session.execute(
            select(circles_mod.CircleInvite)
            .where(circles_mod.CircleInvite.circle_id == circle_id)
            .order_by(circles_mod.CircleInvite.created_at.desc())
        )).scalars().all()
        # No raw tokens on list — only on create.
        return [
            CircleInviteRead(
                id=r.id,
                delivery=r.delivery,
                invited_email=r.invited_email,
                expires_at=r.expires_at,
                used_at=r.used_at,
                raw_token=None,
            )
            for r in rows
        ]


@router.delete("/{circle_id}/invites/{invite_id}", status_code=204)
async def revoke_invite(
    circle_id: int,
    invite_id: int,
    user: User = Depends(current_active_user),
):
    await _require_owner(circle_id, user)
    async with async_session_maker() as session:
        async with session.begin():
            result = await session.execute(
                delete(circles_mod.CircleInvite).where(
                    circles_mod.CircleInvite.id == invite_id,
                    circles_mod.CircleInvite.circle_id == circle_id,
                )
            )
            if result.rowcount == 0:
                raise HTTPException(status_code=404, detail="Invite not found")
    await record("circle_invite_revoke", user_id=user.id)
    return


@router.post("/{circle_id}/join", status_code=201)
async def accept_invite(
    circle_id: int,
    body: CircleJoin,
    user: User = Depends(current_active_user),
):
    import hashlib
    hashed = hashlib.sha256(body.token.encode("utf-8")).hexdigest()
    async with async_session_maker() as session:
        async with session.begin():
            invite = (await session.execute(
                select(circles_mod.CircleInvite).where(
                    circles_mod.CircleInvite.circle_id == circle_id,
                    circles_mod.CircleInvite.token_hash == hashed,
                )
            )).scalar_one_or_none()
            if invite is None:
                raise HTTPException(status_code=404, detail="Invalid invite token")
            if invite.used_at is not None:
                raise HTTPException(status_code=410, detail="Invite already used")
            if circles_mod.is_invite_expired(invite):
                raise HTTPException(status_code=410, detail="Invite expired")
            # Add as member (no-op if already a member).
            existing = (await session.execute(
                select(circles_mod.CircleMember).where(
                    circles_mod.CircleMember.circle_id == circle_id,
                    circles_mod.CircleMember.user_id == str(user.id),
                )
            )).scalar_one_or_none()
            if existing is None:
                session.add(circles_mod.CircleMember(circle_id=circle_id, user_id=user.id))
            invite.used_at = datetime.datetime.now(datetime.timezone.utc)
    await record("circle_invite_accept", user_id=user.id)
    return {"circle_id": circle_id}


@router.get("/{circle_id}/feed", response_model=list[CircleHabitRead])
async def circle_feed(
    circle_id: int,
    days: int = Query(default=14, ge=1, le=90),
    user: User = Depends(current_active_user),
):
    """Per-shared-habit view: streak (if permitted by visibility) and the
    last `days` records (with text only if 'ticks+streak+notes')."""
    circle = await _require_owner_or_member(circle_id, user)
    async with async_session_maker() as session:
        shared = (await session.execute(
            select(circles_mod.CircleHabit)
            .where(circles_mod.CircleHabit.circle_id == circle_id)
        )).scalars().all()
        if not shared:
            return []
        owner = (await session.execute(
            select(User).where(User.id == circle.owner_id)
        )).scalar_one()

    # The owner's habit_list is fetched once for the whole batch.
    try:
        habit_list = await views.get_user_habit_list(owner)
    except HabitListNotFoundError:
        return []

    end = datetime.date.today()
    start = end - datetime.timedelta(days=days - 1)

    out: list[CircleHabitRead] = []
    for ch in shared:
        try:
            habit = await views.get_user_habit(owner, ch.habit_id)
        except Exception:
            continue
        status_map = get_habit_date_completion(habit, start, end)
        records = []
        for day in sorted(status_map.keys()):
            cstatus = status_map[day]
            done = "DONE" in cstatus
            text = None
            if ch.visibility == circles_mod.VIS_TICKS_STREAK_NOTES:
                rec = habit.ticked_data.get(day)
                text = getattr(rec, "text", None) if rec else None
            entry = {"day": day.isoformat(), "done": done}
            if text:
                entry["text"] = text
            records.append(entry)
        # Storage layer doesn't expose a `streak` property; compute from
        # ticked_days so we don't pull in frontend/streaks.py (NiceGUI).
        streak = _current_streak(habit.ticked_days) if ch.visibility in (
            circles_mod.VIS_TICKS_STREAK, circles_mod.VIS_TICKS_STREAK_NOTES
        ) else 0
        out.append(CircleHabitRead(
            habit_id=ch.habit_id,
            owner_email=owner.email,
            name=habit.name,
            visibility=ch.visibility,
            share_notes=ch.share_notes,
            streak=streak,
            records=records,
        ))
    return out


def _current_streak(ticked_days: list[datetime.date]) -> int:
    """Number of consecutive days ending today (or yesterday) that are ticked."""
    if not ticked_days:
        return 0
    s = set(ticked_days)
    today = datetime.date.today()
    streak = 0
    # Allow grace of 1 day (today not yet ticked) so streaks don't
    # snap to zero mid-morning.
    cursor = today if today in s else today - datetime.timedelta(days=1)
    while cursor in s:
        streak += 1
        cursor -= datetime.timedelta(days=1)
    return streak
