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

from agent.database import postgres
from agent.database.notifications import LISTENER
from agent.ui_invalidations import outbox

logger = logging.getLogger(__name__)

REPLAY_MARGIN_SECONDS = 30.0
"""Added to every replay window: covers notification lag and a write that
commits a moment after its row was inserted."""

_PRUNE_INTERVAL_SECONDS = 3600.0


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


class Hub:
    def __init__(self) -> None:
        self._streams: dict[str, set[Stream]] = {}
        self._prune_task: asyncio.Task[None] | None = None
        self._started_at = time.monotonic()
        self._stop = asyncio.Event()

    @contextlib.contextmanager
    def subscribe(self, topics: frozenset[str]) -> Iterator[Stream]:
        """A stream registered for ``topics`` until the block exits.

        Registration comes first, so a caller can replay what it missed afterwards
        without a gap in which a change is neither replayed nor heard.
        """
        stream = Stream(topics)
        for topic in topics:
            self._streams.setdefault(topic, set()).add(stream)
        try:
            yield stream
        finally:
            for topic in topics:
                streams = self._streams.get(topic)
                if streams is not None:
                    streams.discard(stream)
                    if not streams:
                        del self._streams[topic]

    def deliver(self, topics: Iterable[str]) -> None:
        topics = tuple(topics)
        for stream in {stream for topic in topics for stream in self._streams.get(topic, ())}:
            stream.mark(topics)

    async def start(self) -> None:
        if not postgres.configured():
            logger.info("UI invalidations disabled: PostgreSQL is not configured")
            return
        self._started_at = time.monotonic()
        self._stop.clear()
        await LISTENER.listen(outbox.CHANNEL, self._on_notify, self._on_connected)
        if self._prune_task is None or self._prune_task.done():
            self._prune_task = asyncio.create_task(
                self._prune_forever(), name="ui-invalidation-prune"
            )

    async def stop(self) -> None:
        self._stop.set()
        LISTENER.unlisten(outbox.CHANNEL)
        task, self._prune_task = self._prune_task, None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._streams.clear()

    def _on_notify(self, payload: str) -> None:  # pragma: no cover - driven by Postgres
        self.deliver(payload.split("\n"))

    async def _on_connected(self, down_seconds: float | None) -> None:
        if not self._streams:
            return
        unheard = time.monotonic() - self._started_at if down_seconds is None else down_seconds
        age = unheard + REPLAY_MARGIN_SECONDS
        self.deliver(await outbox.invalidated_since(dict.fromkeys(self._streams, age)))

    async def _prune_forever(self) -> None:
        while True:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._stop.wait(), timeout=_PRUNE_INTERVAL_SECONDS)
            if self._stop.is_set():
                return
            try:
                pruned = await outbox.prune()
            except Exception:  # noqa: BLE001
                logger.warning("Pruning UI invalidations failed", exc_info=True)
                continue
            logger.info("Pruned UI invalidations", extra={"pruned_ui_invalidations": pruned})


HUB = Hub()
