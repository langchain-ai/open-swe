"""Collect items from anywhere in the process and handle them in batches."""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable
from threading import Condition, Thread
from typing import Generic, TypeVar

from managed_deepagents._concurrency.deadline import Deadline

T = TypeVar("T")


class BatchProcessor(Generic[T]):
    """Hand pushed items to ``handle`` in batches on one daemon thread.

    A batch is handled when ``max_batch_size`` items are waiting, or
    ``max_wait_seconds`` after the first one arrived. One batch is handled at a
    time; an exception from ``handle`` drops that batch. Compose ``handle``
    (see ``_resilience``) for retries, passing on its deadline, which is set
    by :meth:`close`. The thread starts on the first push, so callers on any
    thread or event loop never block on delivery.
    """

    def __init__(
        self,
        *,
        handle: Callable[[list[T], Deadline], None],
        max_batch_size: int,
        max_wait_seconds: float,
        max_queued: int,
        max_batch_bytes: int | None = None,
        size_of: Callable[[T], int] | None = None,
        name: str = "mda-batch",
    ) -> None:
        self._handle = handle
        self._max_batch_size = max_batch_size
        self._max_wait_seconds = max_wait_seconds
        self._max_queued = max_queued
        self._max_batch_bytes = max_batch_bytes
        self._size_of = size_of
        self._name = name
        self._items: deque[T] = deque()
        self._condition = Condition()
        self._thread: Thread | None = None
        self._handling = False
        self._closed = False
        self._deadline = Deadline()

    def push(self, *items: T) -> int:
        """Queue items without blocking or raising. Returns how many were accepted."""
        with self._condition:
            if self._closed:
                return 0
            accepted = list(items[: max(self._max_queued - len(self._items), 0)])
            self._items.extend(accepted)
            if self._thread is None:
                self._thread = Thread(target=self._run, name=self._name, daemon=True)
                self._thread.start()
            self._condition.notify_all()
            return len(accepted)

    def idle(self, timeout: float) -> bool:
        """Wait up to ``timeout`` until everything pushed so far is handled."""
        with self._condition:
            return self._condition.wait_for(
                lambda: not self._items and not self._handling, timeout=timeout
            )

    def close(self, timeout: float) -> None:
        """Drain: refuse new items and handle what is queued, then stop.

        Batches go a batch at a time as usual but without the batch wait,
        retried as ``handle`` does, with every wait bounded by ``timeout``.
        What is left at the deadline is dropped.
        """
        with self._condition:
            self._closed = True
            self._deadline.set(timeout)
            self._condition.notify_all()
            thread = self._thread
        if thread is not None:
            thread.join(timeout=self._deadline.remaining())
        with self._condition:
            self._items.clear()
            self._condition.notify_all()

    def _next_batch_size(self) -> int:
        """How many queued items fit the next batch.

        With ``size_of``, a batch also stops before it would pass
        ``max_batch_bytes``; a single larger item still goes, alone.
        """
        limit = min(self._max_batch_size, len(self._items))
        if self._max_batch_bytes is None or self._size_of is None:
            return limit
        count = 0
        size = 0
        while count < limit:
            size += self._size_of(self._items[count])
            if count > 0 and size > self._max_batch_bytes:
                break
            count += 1
        return count

    def _run(self) -> None:
        while True:
            with self._condition:
                self._condition.wait_for(lambda: self._items or self._closed)
                if not self._items or self._deadline.remaining() <= 0:
                    self._items.clear()
                    return
                deadline = time.monotonic() + self._max_wait_seconds
                self._condition.wait_for(
                    lambda: len(self._items) >= self._max_batch_size or self._closed,
                    timeout=max(deadline - time.monotonic(), 0),
                )
                batch = [self._items.popleft() for _ in range(self._next_batch_size())]
                self._handling = True
            try:
                self._handle(batch, self._deadline)
            except Exception:  # noqa: BLE001, S110 - a failed batch is dropped
                pass
            finally:
                with self._condition:
                    self._handling = False
                    if not self._items:
                        self._condition.notify_all()
