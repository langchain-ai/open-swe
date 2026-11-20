"""People read once per block, for requests that look the same person up many times.

Starting a Slack turn looks its sender up by Slack account, GitHub login and id a dozen
times over, from helpers that each need the person: the thread's owner, its participants,
the run's owner and its authorization. Each lookup is its own transaction. Within
``SharedUserReads.scope()`` each lookup runs once and later ones share its result.

A write to a person's rows (``users``, ``user_identity``) forgets every shared read,
so a lookup after it reads the database again.
"""

import asyncio
import functools
from collections.abc import AsyncGenerator, Awaitable, Callable, Hashable
from contextlib import asynccontextmanager
from contextvars import ContextVar
from typing import Final, cast


class SharedUserReads:
    """The person lookups one block shares, keyed by what was looked up."""

    _READS: Final[ContextVar[dict[Hashable, asyncio.Future[object]] | None]] = ContextVar(
        "shared_user_reads", default=None
    )

    @classmethod
    @asynccontextmanager
    async def scope(cls) -> AsyncGenerator[None]:
        """Share person lookups for the block; a block inside another shares the outer one's."""
        if cls._READS.get() is not None:
            yield
            return
        token = cls._READS.set({})
        try:
            yield
        finally:
            cls._READS.reset(token)

    @classmethod
    async def read[T](cls, key: Hashable, fetch: Callable[[], Awaitable[T]]) -> T:
        """``fetch()``'s result, fetched once per ``key`` within a scope."""
        reads = cls._READS.get()
        if reads is None:
            return await fetch()
        reading = reads.get(key)
        if reading is None:
            reading = reads[key] = asyncio.ensure_future(fetch())
        try:
            return cast("T", await asyncio.shield(reading))
        except Exception:
            # A failed read is not shared: the next lookup tries again.
            if reads.get(key) is reading:
                del reads[key]
            raise

    @classmethod
    def forget(cls) -> None:
        """Drop every shared read, so the next lookup sees what was just written."""
        reads = cls._READS.get()
        if reads is not None:
            reads.clear()

    @classmethod
    def forgets[**P, R](cls, write: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
        """Decorate a write to a person's rows: shared reads are forgotten once it ends."""

        @functools.wraps(write)
        async def wrapped(*args: P.args, **kwargs: P.kwargs) -> R:
            try:
                return await write(*args, **kwargs)
            finally:
                cls.forget()

        return wrapped
