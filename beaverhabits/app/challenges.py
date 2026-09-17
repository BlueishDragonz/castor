"""Database-backed, browser-bound WebAuthn challenges.

Import this module before Base.metadata.create_all. Every consume opens its own
transaction and commits the conditional delete BEFORE invoking any verifier.
There is deliberately no worker-local security state.
"""
import hashlib
import time

from beaverhabits.app.db import Base, async_session_maker
from fastapi_users_db_sqlalchemy.generics import GUID
from sqlalchemy import String, delete
from sqlalchemy.orm import Mapped, mapped_column


class WebAuthnChallenge(Base):
    __tablename__ = "webauthn_challenge"

    challenge_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    ceremony: Mapped[str] = mapped_column(String(16), nullable=False)
    user_id = mapped_column(GUID, nullable=False, index=True)
    browser_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[float] = mapped_column(nullable=False, index=True)


def _digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


class ChallengeStore:
    async def issue(self, challenge: bytes, ceremony: str, user_id, browser: str,
                    ttl_seconds: float) -> None:
        if ceremony not in {"register", "login"} or not 0 < ttl_seconds <= 300:
            raise ValueError("Invalid ceremony or challenge TTL")
        now = time.time()
        async with async_session_maker() as session:
            # Lazy cleanup bounds expired rows; begin endpoints also need throttles.
            await session.execute(delete(WebAuthnChallenge).where(WebAuthnChallenge.expires_at <= now))
            session.add(WebAuthnChallenge(
                challenge_hash=_digest(challenge), ceremony=ceremony, user_id=user_id,
                browser_hash=_digest(browser.encode()), expires_at=now + ttl_seconds,
            ))
            await session.commit()

    async def consume(self, challenge: bytes, ceremony: str, user_id, browser: str) -> bool:
        async with async_session_maker() as session:
            result = await session.execute(delete(WebAuthnChallenge).where(
                WebAuthnChallenge.challenge_hash == _digest(challenge),
                WebAuthnChallenge.ceremony == ceremony,
                WebAuthnChallenge.user_id == user_id,
                WebAuthnChallenge.browser_hash == _digest(browser.encode()),
                WebAuthnChallenge.expires_at > time.time(),
            ))
            consumed = result.rowcount == 1
            await session.commit()
            return consumed
