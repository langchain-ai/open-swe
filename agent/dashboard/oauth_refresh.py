"""One refresh at a time per stored OAuth credential, across every worker and replica.

Providers that rotate refresh tokens (GitHub, Notion, LangSmith) treat a second use
of a rotated token as theft and revoke the whole grant, so two processes refreshing
the same credential at once can silently disconnect the person. Callers hold this
guard around their read-refresh-write and must re-read the stored credential once
inside it: another process may already have refreshed it.
"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from agent.database import postgres

_local_locks: dict[str, asyncio.Lock] = {}
_lock_engines: dict[str, AsyncEngine] = {}


def _lock_engine(uri: str) -> AsyncEngine:
    """Unpooled: the lock is held across a provider's HTTP call, so it must not occupy
    a connection from the app's shared pool."""
    engine = _lock_engines.get(uri)
    if engine is None:
        engine = create_async_engine(
            uri,
            poolclass=NullPool,
            connect_args={"server_settings": {"application_name": "open-swe-oauth-refresh"}},
        )
        _lock_engines[uri] = engine
    return engine


@asynccontextmanager
async def refresh_guard(provider: str, login: str) -> AsyncIterator[None]:
    key = f"oauth-refresh:{provider}:{login.strip().lower()}"
    # The in-process lock keeps same-worker waiters from each opening a database connection.
    async with _local_locks.setdefault(key, asyncio.Lock()):
        uri = postgres.uri()
        if uri is None:
            yield
            return
        async with _lock_engine(uri).begin() as conn:
            await conn.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:subject, 0))"),
                {"subject": key},
            )
            yield
