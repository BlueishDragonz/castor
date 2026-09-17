"""Minimal persistent security audit trail, without request data or credentials.

Import this module before Base.metadata.create_all. Call append_audit_event only
with a fixed event/outcome and a known account UUID (None for unknown accounts).
It owns a separate committed transaction; call AFTER the business transaction
commits. Failures propagate: callers choose their availability/error policy and
must not log exception text, which may contain database connection details.
Call prune_audit_events periodically as well, to expire records on idle sites.
"""
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, String, delete
from sqlalchemy.orm import Mapped, mapped_column

from beaverhabits.app import db

DEFAULT_RETENTION_DAYS = 90
EVENTS = frozenset({
    "login", "logout", "register", "password_change", "password_reset",
    "password_reset_request", "account_delete", "token_create", "token_revoke",
    "passkey_register", "passkey_delete", "backup",
})
OUTCOMES = frozenset({"success", "failure", "denied"})


def _sql_values(values: frozenset[str]) -> str:
    # Static developer-owned labels only, never user input.
    return ", ".join(repr(value) for value in sorted(values))


class AuditEvent(db.Base):
    __tablename__ = "audit_event"
    __table_args__ = (
        CheckConstraint(f"event IN ({_sql_values(EVENTS)})", name="audit_event_label"),
        CheckConstraint(f"outcome IN ({_sql_values(OUTCOMES)})", name="audit_outcome_label"),
        CheckConstraint("user_id IS NULL OR length(user_id) = 36", name="audit_user_id_length"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True,
        default=lambda: datetime.now(timezone.utc),
    )
    event: Mapped[str] = mapped_column(String(32), nullable=False)
    outcome: Mapped[str] = mapped_column(String(16), nullable=False)
    # Deliberately no FK/cascade: account deletion must not erase its audit event.
    user_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)


def _cutoff(retention_days: int) -> datetime:
    if type(retention_days) is not int or retention_days <= 0:
        raise ValueError("retention_days must be a positive integer")
    return datetime.now(timezone.utc) - timedelta(days=retention_days)


async def append_audit_event(
    event: str,
    outcome: str,
    user_id: UUID | str | None = None,
    *,
    session_factory=None,
    retention_days: int = DEFAULT_RETENTION_DAYS,
) -> AuditEvent:
    """Append and prune atomically; accepts no email, IP, headers or metadata."""
    if not isinstance(event, str) or event not in EVENTS:
        raise ValueError("Unsupported audit event")
    if not isinstance(outcome, str) or outcome not in OUTCOMES:
        raise ValueError("Unsupported audit outcome")
    normalized_id = None
    if user_id is not None:
        try:
            if not isinstance(user_id, (UUID, str)):
                raise ValueError
            normalized_id = str(UUID(str(user_id)))
        except (ValueError, TypeError, AttributeError):
            raise ValueError("Audit user_id must be an account UUID") from None
    cutoff = _cutoff(retention_days)
    factory = session_factory if session_factory is not None else db.async_session_maker
    record = AuditEvent(event=event, outcome=outcome, user_id=normalized_id)
    async with factory() as session:
        async with session.begin():
            await session.execute(delete(AuditEvent).where(AuditEvent.created_at < cutoff))
            session.add(record)
            await session.flush()
            # Return usable values even when the caller's factory expires on commit.
            session.expunge(record)
    return record


async def record(event, outcome="success", user_id=None):
    """Availability policy: report audit failure loudly without undoing committed work."""
    from loguru import logger
    try:
        await append_audit_event(event, outcome, user_id)
    except Exception:
        logger.error("Security audit write failed (event={})", event)


async def audit_retention_task():
    import asyncio
    from loguru import logger
    while True:
        try:
            await prune_audit_events()
        except Exception:
            logger.error("Security audit retention failed")
        await asyncio.sleep(86400)


async def prune_audit_events(
    *, session_factory=None, retention_days: int = DEFAULT_RETENTION_DAYS,
) -> int:
    """Expire old personal identifiers even when there are no new events."""
    cutoff = _cutoff(retention_days)
    factory = session_factory if session_factory is not None else db.async_session_maker
    async with factory() as session:
        async with session.begin():
            result = await session.execute(delete(AuditEvent).where(AuditEvent.created_at < cutoff))
            return result.rowcount
