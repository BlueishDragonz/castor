"""Persistent, atomic fixed-window limits shared by workers; no Redis required."""
import hashlib
import time

from fastapi import Depends, HTTPException, Request
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Mapped, mapped_column
from starlette.responses import JSONResponse
from starlette.requests import HTTPConnection

from beaverhabits.app.db import Base, async_session_maker
from beaverhabits.configs import settings


class RateBucket(Base):
    __tablename__ = 'request_rate_bucket'
    key: Mapped[str] = mapped_column(primary_key=True)
    expires_at: Mapped[int] = mapped_column(index=True)
    count: Mapped[int] = mapped_column(default=1)


# Hard cap per NAMESPACE, not per table: a burst in one namespace (e.g.
# recovery floods) must never starve or evict another namespace's budget
# (api-ip, auth-ip, api-user). Bound: CAP rows per namespace.
CARDINALITY_CAP = 10000


async def consume(namespace, identity, limit, window, *, now=None, sessions=None):
    if limit < 1 or window < 1:
        raise ValueError('Rate limit and window must be positive')
    now = int(time.time() if now is None else now)
    window_end = (now // window + 1) * window
    digest = hashlib.sha256(str(identity).encode()).hexdigest()
    key = f'{namespace}:{digest}:{window_end}'
    async with (sessions or async_session_maker)() as session:
        dialect = session.bind.dialect.name
        if dialect == 'sqlite':
            from sqlalchemy.dialects.sqlite import insert
        elif dialect == 'postgresql':
            from sqlalchemy.dialects.postgresql import insert
        else:
            raise RuntimeError('Unsupported rate-limit database')
        # Transactional cleanup bounds cardinality over time; the per-namespace
        # hard cap prevents churn from forcing unbounded rows within a window.
        await session.execute(delete(RateBucket).where(RateBucket.expires_at <= now))
        count = await session.scalar(
            select(func.count()).select_from(RateBucket).where(RateBucket.key.startswith(f'{namespace}:')))
        if count >= CARDINALITY_CAP and await session.get(RateBucket, key) is None:
            await session.commit()
            return False
        statement = insert(RateBucket).values(key=key, expires_at=window_end, count=1)
        statement = statement.on_conflict_do_update(
            index_elements=[RateBucket.key],
            set_={'count': RateBucket.count + 1},
            where=RateBucket.count < limit,
        ).returning(RateBucket.count)
        admitted = (await session.execute(statement)).scalar_one_or_none() is not None
        await session.commit()
        return admitted


def retry_after(window=60):
    return str(max(1, window - int(time.time()) % window))


async def api_user_limit(request: HTTPConnection):
    if request.scope["type"] == "websocket":
        return
    # Lazy import avoids the existing views/dependencies import cycle.
    from beaverhabits.app.dependencies import current_active_user, get_bearer_token, get_trusted_header_email, get_trusted_local_email
    user = await current_active_user(get_bearer_token(request), get_trusted_header_email(request), get_trusted_local_email())
    try:
        allowed = await consume('api-user', user.id, settings.API_RATE_USER_PER_MINUTE, 60)
    except Exception:
        raise HTTPException(503, 'Rate-limit store unavailable') from None
    if not allowed:
        raise HTTPException(429, 'Rate limit exceeded', headers={'Retry-After': retry_after()})


class IPRateLimitMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        path = scope.get('path', '')
        namespace = 'api-ip' if path.startswith('/api/v1/') else 'auth-ip' if path.startswith('/auth/') and scope['method'] != 'GET' else None
        if namespace:
            # Do not read client-supplied Forwarded/X-Forwarded-For headers.
            ip = (scope.get('client') or ('unknown', 0))[0]
            limit = settings.API_RATE_IP_PER_MINUTE if namespace == 'api-ip' else settings.AUTH_RATE_IP_PER_MINUTE
            try:
                allowed = await consume(namespace, ip, limit, 60)
            except Exception:
                await JSONResponse({'detail': 'Rate-limit store unavailable'}, status_code=503)(scope, receive, send)
                return
            if not allowed:
                await JSONResponse({'detail': 'Rate limit exceeded'}, status_code=429, headers={'Retry-After': retry_after()})(scope, receive, send)
                return
        await self.app(scope, receive, send)
