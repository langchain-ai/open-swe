"""Routing invalidated topics to this process's open streams.

Every replica hears every notification on the shared LISTEN connection and
hands each topic to the streams here that asked for it. While that connection
is down, other replicas' invalidations reach nobody here, so on reconnect the
hub asks the outbox which subscribed topics were invalidated in the gap.
"""

import asyncio
import contextlib
import logging
import math
import time
from collections.abc import Iterable, Iterator, Mapping

from openswe.database.notifications import LISTENER
from openswe.ui_invalidations import outbox
from openswe.ui_invalidations.topics import BaseTopic

logger = logging.getLogger(__name__)

REPLAY_MARGIN_SECONDS = 30.0
"""Added to every replay window: covers notification lag and a write that
commits a moment after its row was inserted."""

_PRUNE_INTERVAL_SECONDS = 3600.0


class Stream:
    """One reader's topics, and the ones invalidated since it last drained.

    A topic with a minimum interval (``BaseTopic.min_interval``) is sent at once,
    then held until that interval has passed since it was last sent, so a burst of
    changes reaches the reader as its first change and the state after its last.
    """

    __slots__ = ("_changed", "_held_until", "_intervals", "_wake", "topics")

    def __init__(self, topics: frozenset[str]) -> None:
        self.topics = topics
        self._changed: set[str] = set()
        self._wake = asyncio.Event()
        self._intervals = {
            topic: interval for topic in topics if (interval := BaseTopic.min_interval(topic)) > 0
        }
        self._held_until: dict[str, float] = {}

    def hold_fresh(self, ages: Mapping[str, float]) -> None:
        """Hold each topic with an interval until that long after the reader's data for it.

        A reader reconnects whenever its topics change, so without this every
        reconnect would resend what the previous stream sent a moment ago.
        """
        now = time.monotonic()
        for topic, interval in self._intervals.items():
            if topic in ages:
                self._held_until[topic] = now - ages[topic] + interval

    def mark(self, topics: Iterable[str]) -> None:
        self._changed.update(topic for topic in topics if topic in self.topics)
        if self._changed:
            self._wake.set()

    async def drain(self, timeout: float) -> set[str]:
        """What may be sent, waiting up to ``timeout`` for something to be; empty on timeout."""
        deadline = time.monotonic() + timeout
        while (remaining := deadline - time.monotonic()) > 0:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(
                    self._wake.wait(), timeout=min(remaining, self._next_release())
                )
            if ready := self.take():
                return ready
        return set()

    def take(self) -> set[str]:
        """What changed and may be sent now; a held topic stays until its interval passes."""
        now = time.monotonic()
        ready = {topic for topic in self._changed if self._held_until.get(topic, 0.0) <= now}
        self._changed -= ready
        self._wake.clear()
        for topic in self._intervals.keys() & ready:
            self._held_until[topic] = now + self._intervals[topic]
        return ready

    def _next_release(self) -> float:
        """Seconds until the first changed topic may be sent; infinite when none changed."""
        now = time.monotonic()
        return min(
            (max(self._held_until.get(topic, 0.0) - now, 0.0) for topic in self._changed),
            default=math.inf,
        )


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
