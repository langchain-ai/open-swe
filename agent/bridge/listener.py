"""Waking whoever waits on a bridge: the CLI's long poll, and the graph's request.

Correctness comes from Postgres: every state change notifies inside the
transaction that made it, and the process's shared LISTEN connection
(``agent.database.notifications``) hands the notified ids to whoever subscribed
to the bridge. The notification carries ids only, so a subscriber always reads the rows
itself.

A gap in the notifications costs latency, not correctness: a long poll re-claims
when it wakes or times out, and a waiting request re-reads its row on every
liveness tick, so neither depends on a notification arriving.

The same task prunes bridges whose heartbeat has stopped, which is what turns a
machine that went away mid-run into a failed request rather than a waiter that
never returns.
"""

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator

from agent.bridge.constants import ALIVE_THRESHOLD_SECONDS, CHANNEL
from agent.database import notifications, postgres

logger = logging.getLogger(__name__)

_QUEUE_LIMIT = 256

BridgeEvent = tuple[str, str]
"""``(request_id, event)``; ``request_id`` is empty for a bridge-wide event."""

_SUBSCRIBERS: dict[str, set[asyncio.Queue[BridgeEvent]]] = {}
_PRUNE_TASK: asyncio.Task[None] | None = None
_STOP = asyncio.Event()


class _Events:
    """The events handed to one subscriber, in the order they arrived."""

    __slots__ = ("_queue",)

    def __init__(self, queue: asyncio.Queue[BridgeEvent]) -> None:
        self._queue = queue

    def __aiter__(self) -> _Events:
        return self

    async def __anext__(self) -> BridgeEvent:
        return await self._queue.get()


@contextlib.asynccontextmanager
async def subscribe(bridge_id: str) -> AsyncIterator[AsyncIterator[BridgeEvent]]:
    """Events notified for ``bridge_id`` for the body of the ``with``.

    Registration happens on entry, so a caller can subscribe and then enqueue or
    claim without a window in which the wake-up it is waiting for is lost.
    """
    queue: asyncio.Queue[BridgeEvent] = asyncio.Queue(maxsize=_QUEUE_LIMIT)
    _SUBSCRIBERS.setdefault(bridge_id, set()).add(queue)
    try:
        yield _Events(queue)
    finally:
        queues = _SUBSCRIBERS.get(bridge_id)
        if queues is not None:
            queues.discard(queue)
            if not queues:
                _SUBSCRIBERS.pop(bridge_id, None)


def publish(bridge_id: str, request_id: str, event: str) -> None:
    """Hand one event to this process's subscribers for ``bridge_id``.

    A full queue gives up its oldest entry rather than this one: a subscriber
    re-reads its rows when it wakes, so an older event it never sees costs it
    nothing, while the newest is what ends its wait.
    """
    for queue in tuple(_SUBSCRIBERS.get(bridge_id, ())):
        try:
            queue.put_nowait((request_id, event))
        except asyncio.QueueFull:
            with contextlib.suppress(asyncio.QueueEmpty):
                queue.get_nowait()
            with contextlib.suppress(asyncio.QueueFull):
                queue.put_nowait((request_id, event))


async def start() -> None:
    global _PRUNE_TASK
    if not postgres.configured():
        logger.info("Sandbox bridge listener disabled: PostgreSQL is not configured")
        return
    _STOP.clear()
    await notifications.listen(CHANNEL, _on_notify)
    if _PRUNE_TASK is None or _PRUNE_TASK.done():
        _PRUNE_TASK = asyncio.create_task(_prune_forever(), name="bridge-prune")


async def stop() -> None:
    global _PRUNE_TASK
    _STOP.set()
    notifications.unlisten(CHANNEL)
    task = _PRUNE_TASK
    _PRUNE_TASK = None
    if task is not None:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    _SUBSCRIBERS.clear()


def _on_notify(payload: str) -> None:  # pragma: no cover - driven by Postgres
    bridge_id, _, rest = payload.partition(":")
    request_id, _, event = rest.partition(":")
    if not bridge_id or not event:
        logger.warning(
            "Ignored an unreadable sandbox bridge notification",
            extra={"bridge_notification": payload},
        )
        return
    publish(bridge_id, request_id, event)


async def _prune_forever() -> None:
    """Rotate request partitions, then close bridges that stopped heartbeating."""
    # Imported here because the store notifies through this module.
    from agent.bridge.store import BridgeStore

    while not _STOP.is_set():
        try:
            await asyncio.wait_for(_STOP.wait(), timeout=ALIVE_THRESHOLD_SECONDS)
        except TimeoutError:
            pass
        if _STOP.is_set():
            return
        try:
            await BridgeStore.ensure_partitions()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.warning("Rotating sandbox bridge request partitions failed", exc_info=True)
        try:
            pruned = await BridgeStore.prune_stale()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            # A failed sweep retries on the next tick; a waiter on a dead bridge
            # still gives up on its own timeout.
            logger.warning("Pruning stale sandbox bridges failed", exc_info=True)
            continue
        if pruned:
            logger.info("Closed stale sandbox bridges", extra={"pruned_bridges": pruned})
