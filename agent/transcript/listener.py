"""Waking SSE subscribers when a thread's log grows.

Correctness comes from Postgres: ``append`` issues ``pg_notify`` inside its
transaction, and the process's shared LISTEN connection
(``agent.database.notifications``) hands the notified version to whoever
subscribed to the thread. The notification carries ids only, so a subscriber
always reads the rows itself.

``append`` also publishes in-process. That is an optimisation — the graph and
the HTTP app share a process, so the common case does not have to wait for the
round trip — and it is deliberately the same code path as a notification, so a
missing in-process publish only ever costs latency.
"""

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator

from sqlalchemy import ARRAY, Text, bindparam, text

from agent.database import postgres
from agent.database.notifications import LISTENER

logger = logging.getLogger(__name__)

CHANNEL = "open_swe_thread_events"
"""LISTEN/NOTIFY channel carrying ``<thread_id>:<version>`` — ids only, never content."""

DELETED = "deleted"
"""Notification payload suffix for a thread whose transcript was deleted."""

DELETED_VERSION = -1
"""The version a subscriber receives when the thread it follows is deleted."""

_QUEUE_LIMIT = 256

_SUBSCRIBERS: dict[str, set[asyncio.Queue[int]]] = {}


class _Versions:
    """The versions handed to one subscriber, in the order they arrived."""

    __slots__ = ("_queue",)

    def __init__(self, queue: asyncio.Queue[int]) -> None:
        self._queue = queue

    def __aiter__(self) -> _Versions:
        return self

    async def __anext__(self) -> int:
        return await self._queue.get()


@contextlib.asynccontextmanager
async def subscribe(thread_id: str) -> AsyncIterator[AsyncIterator[int]]:
    """Versions notified for ``thread_id``, as they arrive, for the body of the ``with``.

    Registration happens on entry, so a caller can subscribe and then read its
    replay range without a window in which events are lost. Leaving the block
    unregisters, whether or not the versions were ever awaited: a subscriber
    that gives up during its replay must not leave its queue behind.
    """
    queue: asyncio.Queue[int] = asyncio.Queue(maxsize=_QUEUE_LIMIT)
    _SUBSCRIBERS.setdefault(thread_id, set()).add(queue)
    try:
        yield _Versions(queue)
    finally:
        queues = _SUBSCRIBERS.get(thread_id)
        if queues is not None:
            queues.discard(queue)
            if not queues:
                _SUBSCRIBERS.pop(thread_id, None)


def publish(thread_id: str, version: int) -> None:
    """Hand ``version`` to this process's subscribers for ``thread_id``.

    A full queue gives up its oldest entry rather than this one. A subscriber
    reads rows by version, so an older notification it never sees costs it
    nothing — but the newest, and ``DELETED_VERSION`` above all, has to arrive
    or it waits on an append that may never come.
    """
    for queue in tuple(_SUBSCRIBERS.get(thread_id, ())):
        try:
            queue.put_nowait(version)
        except asyncio.QueueFull:
            with contextlib.suppress(asyncio.QueueEmpty):
                queue.get_nowait()
            with contextlib.suppress(asyncio.QueueFull):
                queue.put_nowait(version)


async def start() -> None:
    if not postgres.configured():
        logger.info("Transcript listener disabled: PostgreSQL is not configured")
        return
    await LISTENER.listen(CHANNEL, _on_notify, _on_connected)


async def stop() -> None:
    LISTENER.unlisten(CHANNEL)
    _SUBSCRIBERS.clear()


def _on_notify(payload: str) -> None:  # pragma: no cover - driven by Postgres
    thread_id, _, version = payload.rpartition(":")
    if thread_id and version == DELETED:
        publish(thread_id, DELETED_VERSION)
        return
    if not thread_id or not version.isdigit():
        logger.warning(
            "Ignored an unreadable transcript notification",
            extra={"transcript": {"notification": payload}},
        )
        return
    publish(thread_id, int(version))


async def _on_connected(_down_seconds: float | None) -> None:
    await _resync_subscribers()


async def _resync_subscribers() -> None:
    """Publish each subscribed thread's head after a gap in the notifications.

    While the listener was disconnected, an append in another process notified
    nobody here. A subscriber reads rows by version, so handing it the current
    head is enough to carry it past everything it missed.

    A subscribed thread with no row at all lost its ``deleted`` notification
    the same way, and would otherwise sit on heartbeats forever: a subscriber
    registers before it replays, so a missing head means the thread is gone
    rather than not yet written.
    """
    thread_ids = tuple(_SUBSCRIBERS)
    if not thread_ids:
        return
    async with postgres.read_only_transaction() as conn:
        rows = await conn.execute(
            text(
                "SELECT thread_id, version FROM thread WHERE thread_id = ANY(:thread_ids)"
            ).bindparams(bindparam("thread_ids", type_=ARRAY(Text))),
            {"thread_ids": list(thread_ids)},
        )
        heads = {row.thread_id: row.version for row in rows}
    for thread_id in thread_ids:
        publish(thread_id, heads.get(thread_id, DELETED_VERSION))
