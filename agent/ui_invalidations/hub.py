"""Routing invalidated topics to this process's open streams.

Every replica hears every notification on the shared LISTEN connection and
hands each topic to the streams here that asked for it. While that connection
is down, other replicas' invalidations reach nobody here, so on reconnect the
hub asks the outbox which subscribed topics were invalidated in the gap.
"""

import asyncio
import contextlib
import logging
import time
from collections.abc import Iterable, Iterator

from agent.database import notifications, postgres
from agent.ui_invalidations import outbox

logger = logging.getLogger(__name__)

REPLAY_MARGIN_SECONDS = 30.0
"""Added to every replay window: covers notification lag and a write that
commits a moment after its row was inserted."""

_PRUNE_INTERVAL_SECONDS = 3600.0

_STREAMS: dict[str, set[Stream]] = {}
_PRUNE_TASK: asyncio.Task[None] | None = None
_STARTED_AT = time.monotonic()
_STOP = asyncio.Event()


class Stream:
    """One reader's topics, and the ones invalidated since it last drained."""

    __slots__ = ("_changed", "_wake", "topics")

    def __init__(self, topics: frozenset[str]) -> None:
        self.topics = topics
        self._changed: set[str] = set()
        self._wake = asyncio.Event()

    def mark(self, topics: Iterable[str]) -> None:
        self._changed.update(topic for topic in topics if topic in self.topics)
        if self._changed:
            self._wake.set()

    async def drain(self, timeout: float) -> set[str]:
        """Whatever changed, waiting up to ``timeout`` for something to; empty on timeout."""
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._wake.wait(), timeout=timeout)
        return self.take()

    def take(self) -> set[str]:
        changed, self._changed = self._changed, set()
        self._wake.clear()
        return changed


@contextlib.contextmanager
def subscribe(topics: frozenset[str]) -> Iterator[Stream]:
    """A stream registered for ``topics`` until the block exits.

    Registration comes first, so a caller can replay what it missed afterwards
    without a gap in which a change is neither replayed nor heard.
    """
    stream = Stream(topics)
    for topic in topics:
        _STREAMS.setdefault(topic, set()).add(stream)
    try:
        yield stream
    finally:
        for topic in topics:
            streams = _STREAMS.get(topic)
            if streams is not None:
                streams.discard(stream)
                if not streams:
                    _STREAMS.pop(topic, None)


def deliver(topics: Iterable[str]) -> None:
    for topic in topics:
        for stream in tuple(_STREAMS.get(topic, ())):
            stream.mark((topic,))


async def start() -> None:
    global _PRUNE_TASK, _STARTED_AT
    if not postgres.configured():
        logger.info("UI invalidations disabled: PostgreSQL is not configured")
        return
    _STARTED_AT = time.monotonic()
    _STOP.clear()
    await notifications.listen(outbox.CHANNEL, _on_notify, _on_connected)
    if _PRUNE_TASK is None or _PRUNE_TASK.done():
        _PRUNE_TASK = asyncio.create_task(_prune_forever(), name="ui-invalidation-prune")


async def stop() -> None:
    global _PRUNE_TASK
    _STOP.set()
    notifications.unlisten(outbox.CHANNEL)
    task = _PRUNE_TASK
    _PRUNE_TASK = None
    if task is not None:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
    _STREAMS.clear()


def _on_notify(payload: str) -> None:  # pragma: no cover - driven by Postgres
    deliver(payload.split("\n"))


async def _on_connected(down_seconds: float | None) -> None:
    if not _STREAMS:
        return
    unheard = time.monotonic() - _STARTED_AT if down_seconds is None else down_seconds
    age = unheard + REPLAY_MARGIN_SECONDS
    deliver(await outbox.invalidated_since(dict.fromkeys(_STREAMS, age)))


async def _prune_forever() -> None:
    while not _STOP.is_set():
        try:
            await asyncio.wait_for(_STOP.wait(), timeout=_PRUNE_INTERVAL_SECONDS)
        except TimeoutError:
            pass
        if _STOP.is_set():
            return
        try:
            pruned = await outbox.prune()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.warning("Pruning UI invalidations failed", exc_info=True)
            continue
        logger.info("Pruned UI invalidations", extra={"pruned_ui_invalidations": pruned})
