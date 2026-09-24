"""Announcing that a thread's run started or ended, so live sidebars can refresh it.

A notification carries the thread id only; a subscriber re-reads the thread and
decides for itself whether its viewer may see it. ``publish_thread_changed``
hands the id to this process's subscribers and ``pg_notify``s it so every other
replica's listener (``agent.transcript.listener``) does the same.

Kept free of imports from the rest of ``agent.threads``: the transcript
listener imports this module.
"""

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator

from sqlalchemy import text

from agent.database import postgres

logger = logging.getLogger(__name__)

CHANNEL = "open_swe_thread_changed"
"""LISTEN/NOTIFY channel whose payload is a thread id."""

RESYNC = ""
"""Delivered to every subscriber when notifications may have been missed."""

_QUEUE_LIMIT = 256
_NOTIFY_TIMEOUT_SECONDS = 2.0

_SUBSCRIBERS: set[asyncio.Queue[str]] = set()


class _ThreadIds:
    __slots__ = ("_queue",)

    def __init__(self, queue: asyncio.Queue[str]) -> None:
        self._queue = queue

    def __aiter__(self) -> _ThreadIds:
        return self

    async def __anext__(self) -> str:
        return await self._queue.get()


@contextlib.asynccontextmanager
async def subscribe() -> AsyncIterator[AsyncIterator[str]]:
    """Changed thread ids, and ``RESYNC``, as they arrive, for the body of the ``with``."""
    queue: asyncio.Queue[str] = asyncio.Queue(maxsize=_QUEUE_LIMIT)
    _SUBSCRIBERS.add(queue)
    try:
        yield _ThreadIds(queue)
    finally:
        _SUBSCRIBERS.discard(queue)


def publish_local(thread_id: str) -> None:
    """Hand ``thread_id`` to this process's subscribers, dropping a full queue's oldest."""
    for queue in tuple(_SUBSCRIBERS):
        try:
            queue.put_nowait(thread_id)
        except asyncio.QueueFull:
            with contextlib.suppress(asyncio.QueueEmpty):
                queue.get_nowait()
            with contextlib.suppress(asyncio.QueueFull):
                queue.put_nowait(thread_id)


async def publish_thread_changed(thread_id: str) -> None:
    """Tell every replica's live sidebars that ``thread_id`` changed. Never raises.

    The local publish comes first and is all a deployment without PostgreSQL
    gets. This replica's listener hears the notification too, so its
    subscribers see the id twice; the stream coalesces repeats.
    """
    publish_local(thread_id)
    if not postgres.configured():
        return
    try:
        async with asyncio.timeout(_NOTIFY_TIMEOUT_SECONDS), postgres.transaction() as conn:
            await conn.execute(
                text("SELECT pg_notify(:channel, :payload)"),
                {"channel": CHANNEL, "payload": thread_id},
            )
    except Exception:  # noqa: BLE001
        # Other replicas' sidebars fall back to refetching on focus.
        logger.warning(
            "Could not notify other replicas of a thread change",
            exc_info=True,
            extra={"thread_id": thread_id},
        )
