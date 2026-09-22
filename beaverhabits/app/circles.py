"""Private Circle data model.

A Circle is a small trusted group (3-8 members, no hard cap) whose members
can see each other's habit completion data, current streak, and opt-in
notes. Circles are NEVER public and NEVER discoverable by email — membership
requires an explicit invite.

Design constraints:
  - Habit ownership stays with the original user. Circles only grant
    *visibility* on a per-habit basis (via CircleHabit).
  - Notes (HabitRecord.text) are opt-in per CircleHabit row.
  - Membership is bilateral: only the owner can add habits, only the
    owner can remove them. Members can leave the circle at any time.
  - Invites are single-use tokens (CircleInvite). Email and link delivery
    are both supported; this module only mints + consumes the token.
  - All circle events go through audit.record() for the security trail.

This module owns only the schema and the read-side filters. Route handlers
live in app/circle_routes.py.
"""
import datetime
import secrets
from typing import Optional
from uuid import UUID

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    String,
    UniqueConstraint,
    select,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base, TimestampMixin, User, async_session_maker
from .audit import record


# Visibility levels a member has on a shared habit.
# Stored as a string column with a CHECK constraint.
VIS_TICKS = "ticks"
VIS_TICKS_STREAK = "ticks+streak"
VIS_TICKS_STREAK_NOTES = "ticks+streak+notes"
VISIBILITY_LEVELS = frozenset({VIS_TICKS, VIS_TICKS_STREAK, VIS_TICKS_STREAK_NOTES})


def _sql_values(values) -> str:
    # Static developer-owned labels only, never user input.
    return ", ".join(repr(value) for value in sorted(values))


class Circle(TimestampMixin, Base):
    __tablename__ = "circle"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    # Owner is the user who created the circle. Owner can delete the circle,
    # remove habits, remove members, and revoke invites. Ownership is
    # NOT transferable in v1.
    owner_id = mapped_column(
        ForeignKey("user.id", ondelete="CASCADE"), index=True, nullable=False
    )
    owner: Mapped[User] = relationship("User", foreign_keys=[owner_id])

    members: Mapped[list["CircleMember"]] = relationship(
        back_populates="circle",
        uselist=True,
        cascade="all, delete-orphan",
    )
    habits: Mapped[list["CircleHabit"]] = relationship(
        back_populates="circle",
        uselist=True,
        cascade="all, delete-orphan",
    )
    invites: Mapped[list["CircleInvite"]] = relationship(
        back_populates="circle",
        uselist=True,
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        CheckConstraint("length(name) >= 1 AND length(name) <= 80", name="circle_name_length"),
    )


class CircleMember(TimestampMixin, Base):
    """A user who has joined a circle. Membership is unique per (circle, user)."""

    __tablename__ = "circle_member"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    circle_id = mapped_column(
        ForeignKey("circle.id", ondelete="CASCADE"), index=True, nullable=False
    )
    user_id = mapped_column(
        ForeignKey("user.id", ondelete="CASCADE"), index=True, nullable=False
    )

    circle: Mapped[Circle] = relationship(back_populates="members")
    user: Mapped[User] = relationship("User")

    __table_args__ = (
        UniqueConstraint("circle_id", "user_id", name="circle_member_unique"),
    )


class CircleHabit(TimestampMixin, Base):
    """A habit the owner has chosen to share with their circle members.

    visibility controls what members can see:
      - 'ticks': just today/yesterday ticks (boolean per day)
      - 'ticks+streak': ticks + current streak count
      - 'ticks+streak+notes': ticks + streak + HabitRecord.text

    Notes are stored on HabitRecord.text (already part of the habit schema).
    share_notes is a legacy column kept for migration symmetry — the
    visibility string is the source of truth now.
    """

    __tablename__ = "circle_habit"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    circle_id = mapped_column(
        ForeignKey("circle.id", ondelete="CASCADE"), index=True, nullable=False
    )
    # habit_id is the string ID from HabitListModel.data — habits live
    # in JSON, not their own table. We store the string verbatim.
    habit_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    # owner_user_id is denormalised for fast "is the owner of this habit
    # in my circles" joins — owner of the habit equals the circle owner.
    owner_user_id = mapped_column(
        ForeignKey("user.id", ondelete="CASCADE"), index=True, nullable=False
    )
    visibility: Mapped[str] = mapped_column(String(32), nullable=False, default=VIS_TICKS)
    # Legacy flag kept as a redundant column for migration safety.
    # visibility='ticks+streak+notes' implies share_notes=True.
    share_notes: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    circle: Mapped[Circle] = relationship(back_populates="habits")

    __table_args__ = (
        UniqueConstraint("circle_id", "habit_id", name="circle_habit_unique"),
        CheckConstraint(
            f"visibility IN ({_sql_values(VISIBILITY_LEVELS)})",
            name="circle_habit_visibility",
        ),
    )


class CircleInvite(TimestampMixin, Base):
    """Single-use invite token for joining a circle.

    Two delivery modes:
      - 'link': the invitee gets a URL with the token; they accept by
        POST /circles/{id}/join with that token.
      - 'email': we mint the token, the caller (or an outbound email
        job) is responsible for delivering it. Same accept flow.

    Token is stored hashed (SHA-256) so a leaked DB doesn't leak the
    invite. expires_at is enforced at accept time.
    """

    __tablename__ = "circle_invite"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    circle_id = mapped_column(
        ForeignKey("circle.id", ondelete="CASCADE"), index=True, nullable=False
    )
    # Token hash (SHA-256 hex). Raw token is shown to the inviter ONCE
    # at create time and never stored.
    token_hash: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    delivery: Mapped[str] = mapped_column(String(8), nullable=False, default="link")
    invited_email: Mapped[str | None] = mapped_column(String(254), nullable=True)
    expires_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    used_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # User that minted the invite (always the circle owner in v1).
    invited_by = mapped_column(ForeignKey("user.id", ondelete="CASCADE"), nullable=False)

    circle: Mapped[Circle] = relationship(back_populates="invites")

    __table_args__ = (
        CheckConstraint(
            "delivery IN ('link', 'email')", name="circle_invite_delivery"
        ),
    )


# ----------------------------------------------------------------------
# Read-side helpers (no DB writes — only audit.record() may fire).
# ----------------------------------------------------------------------


async def is_member(circle_id: int, user_id: UUID | str) -> bool:
    async with async_session_maker() as session:
        row = (await session.execute(
            select(CircleMember).where(
                CircleMember.circle_id == circle_id,
                CircleMember.user_id == str(user_id),
            )
        )).scalar_one_or_none()
        return row is not None


async def get_visible_habits(user_id: UUID | str) -> list[CircleHabit]:
    """All CircleHabit rows a user can see, across every circle they belong to."""
    async with async_session_maker() as session:
        rows = (await session.execute(
            select(CircleHabit)
            .join(CircleMember, CircleMember.circle_id == CircleHabit.circle_id)
            .where(CircleMember.user_id == str(user_id))
        )).scalars().all()
        return list(rows)


def visibility_allows(visibility: str, want: str) -> bool:
    """Visibility level comparison. Higher tiers include lower tiers."""
    tiers = {VIS_TICKS: 0, VIS_TICKS_STREAK: 1, VIS_TICKS_STREAK_NOTES: 2}
    return tiers.get(visibility, -1) >= tiers.get(want, 99)


def mint_invite_token() -> tuple[str, str]:
    """Returns (raw_token, token_hash). raw_token is shown ONCE."""
    raw = secrets.token_urlsafe(32)
    import hashlib
    h = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return raw, h


def is_invite_expired(invite: CircleInvite) -> bool:
    return datetime.datetime.now(datetime.timezone.utc) >= invite.expires_at
