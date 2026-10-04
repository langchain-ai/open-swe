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

from agent.database import postgres

_local_locks: dict[str, asyncio.Lock] = {}


@asynccontextmanager
async def refresh_guard(provider: str, login: str) -> AsyncIterator[None]:
    key = f"oauth-refresh:{provider}:{login.strip().lower()}"
    # The in-process lock keeps same-worker waiters from each holding a database connection.
    async with _local_locks.setdefault(key, asyncio.Lock()):
        if not postgres.configured():
            yield
            return
        async with postgres.transaction() as conn:
            await conn.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:subject, 0))"),
                {"subject": key},
            )
            yield
