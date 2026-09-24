"""Waking sidebar streams when a ``thread_index`` row changes.

Every index write issues ``pg_notify`` on :data:`CHANNEL` in its transaction.
The transcript listener's connection LISTENs on this channel as well and hands
each notification to :func:`on_notify`. There is one subscriber set for the
whole process: every stream sees every change and filters it by its viewer,
because who may see a row depends on the row, not on the notification.

A notification carries ids only, so a subscriber always re-reads the row.
"""

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass

logger = logging.getLogger(__name__)

CHANNEL = "open_swe_thread_index"
"""LISTEN/NOTIFY channel carrying ``<thread_id>:<seq>`` or ``<thread_id>:deleted``."""

DELETED = "deleted"

_QUEUE_LIMIT = 1024


@dataclass(frozen=True, slots=True)
class IndexChange:
    thread_id: str
    seq: int | None
    """``None`` when the row was deleted."""


class IndexSubscription:
    """The changes handed to one stream, and whether it has missed any.

    ``stale`` is set when a change was dropped for a full queue or the listener
    reconnected; the stream then tells its client to refetch.
    """

    __slots__ = ("_queue", "stale")

    def __init__(self) -> None:
        self._queue: asyncio.Queue[IndexChange | None] = asyncio.Queue(maxsize=_QUEUE_LIMIT)
        self.stale = False

    def offer(self, item: IndexChange | None) -> None:
        try:
            self._queue.put_nowait(item)
        except asyncio.QueueFull:
            self.stale = True
            with contextlib.suppress(asyncio.QueueEmpty):
                self._queue.get_nowait()
            with contextlib.suppress(asyncio.QueueFull):
                self._queue.put_nowait(item)

    def mark_stale(self) -> None:
        self.stale = True
        # ``None`` only wakes a stream waiting on the queue; ``stale`` is the signal.
        self.offer(None)

    async def next(self) -> IndexChange | None:
        """The next change, or ``None`` when the only news is that ``stale`` was set."""
        return await self._queue.get()

    def pending(self, limit: int) -> list[IndexChange | None]:
        """Up to ``limit`` changes that are already queued, without waiting."""
        items: list[IndexChange | None] = []
        while len(items) < limit:
            try:
                items.append(self._queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        return items


_SUBSCRIBERS: set[IndexSubscription] = set()


@contextlib.asynccontextmanager
async def subscribe() -> AsyncIterator[IndexSubscription]:
    """Every index change from entry until the block is left."""
    subscription = IndexSubscription()
    _SUBSCRIBERS.add(subscription)
    try:
        yield subscription
    finally:
        _SUBSCRIBERS.discard(subscription)


def publish(thread_id: str, seq: int | None) -> None:
    """Hand a change to every subscriber in this process; ``seq=None`` for a delete."""
    change = IndexChange(thread_id=thread_id, seq=seq)
    for subscription in tuple(_SUBSCRIBERS):
        subscription.offer(change)


def resync_subscribers() -> None:
    """Tell every subscriber it may have missed changes, after a gap in notifications."""
    for subscription in tuple(_SUBSCRIBERS):
        subscription.mark_stale()


def on_notify(
    _connection: object, _pid: int, _channel: str, payload: str
) -> None:  # pragma: no cover - driven by Postgres
    thread_id, _, seq = payload.rpartition(":")
    if thread_id and seq == DELETED:
        publish(thread_id, None)
        return
    if not thread_id or not seq.isdigit():
        logger.warning(
            "Ignored an unreadable thread index notification",
            extra={"thread_index": {"notification": payload}},
        )
        return
    publish(thread_id, int(seq))
