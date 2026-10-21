"""Bounded telemetry delivery independent of the caller's event loop."""

from __future__ import annotations

from collections import deque
from threading import Condition, Thread
from typing import TYPE_CHECKING, Generic, TypeVar

from managed_deepagents._telemetry.event import TelemetryEvent

if TYPE_CHECKING:
    from managed_deepagents._telemetry.client import TelemetryClient

MAX_PENDING_EVENTS = 128
SHUTDOWN_TIMEOUT_SECONDS = 1.0
E = TypeVar("E", bound=TelemetryEvent)


class BackgroundTelemetryClient(Generic[E]):
    def __init__(self, client: TelemetryClient[E]) -> None:
        self._client = client
        self._queue: deque[E] = deque()
        self._condition = Condition()
        self._thread: Thread | None = None
        self._sending = False
        self._closed = False

    def record(self, event: E) -> None:
        with self._condition:
            if self._closed or len(self._queue) >= MAX_PENDING_EVENTS:
                return
            if self._thread is None:
                self._thread = Thread(target=self._send, name="mda-telemetry", daemon=True)
                self._thread.start()
            self._queue.append(event)
            self._condition.notify_all()

    def flush(self, timeout: float = SHUTDOWN_TIMEOUT_SECONDS) -> bool:
        with self._condition:
            return self._condition.wait_for(
                lambda: not self._queue and not self._sending, timeout=timeout
            )

    def close(self, timeout: float = SHUTDOWN_TIMEOUT_SECONDS) -> None:
        with self._condition:
            self._closed = True
            self._condition.notify_all()
            thread = self._thread
        if thread is not None:
            thread.join(timeout=timeout)

    def _send(self) -> None:
        while True:
            with self._condition:
                self._condition.wait_for(lambda: self._queue or self._closed)
                if not self._queue:
                    return
                event = self._queue.popleft()
                self._sending = True
            try:
                # Synchronous delivery keeps DNS on this daemon thread. An
                # asyncio executor would make Python wait for DNS at exit.
                self._client.record(event)
            finally:
                with self._condition:
                    self._sending = False
                    self._condition.notify_all()
