"""Telemetry sinks drain at exit under one shared deadline."""

from __future__ import annotations

import threading
import time
from typing import TYPE_CHECKING

import pytest

from managed_deepagents._concurrency import BatchProcessor
from managed_deepagents._telemetry.lifecycle import (
    on_shutdown,
    reset_shutdown_for_tests,
    shutdown_telemetry,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from managed_deepagents._concurrency import Deadline


@pytest.fixture(autouse=True)
def _reset() -> Iterator[None]:
    reset_shutdown_for_tests()
    yield
    reset_shutdown_for_tests()


def _hanging() -> BatchProcessor[int]:
    """A processor whose request waits out the deadline."""

    def handle(_batch: list[int], deadline: Deadline) -> None:
        deadline.wait(30)

    processor = BatchProcessor[int](
        handle=handle, max_batch_size=10, max_wait_seconds=30, max_queued=10
    )
    processor.push(1)
    return processor


def test_drains_every_sink_in_parallel_under_one_deadline() -> None:
    hooks: list[Callable[[], None]] = []
    logs, events = _hanging(), _hanging()
    on_shutdown(logs.close, register=hooks.append)
    on_shutdown(events.close, register=hooks.append)

    started = time.monotonic()
    shutdown_telemetry(0.3)
    elapsed = time.monotonic() - started

    assert 0.25 <= elapsed < 0.55
    assert logs.push(2) == 0
    assert events.push(2) == 0


def test_installs_one_exit_hook_that_shuts_down() -> None:
    hooks: list[Callable[[], None]] = []
    drained = threading.Event()
    deadlines: list[float] = []

    def drain(seconds: float) -> None:
        deadlines.append(seconds)
        drained.set()

    on_shutdown(drain, register=hooks.append)
    on_shutdown(drain, register=hooks.append)

    assert hooks == [shutdown_telemetry]
    hooks[0]()

    assert drained.is_set()
    assert deadlines == [5.0, 5.0]


def test_a_failing_drain_does_not_stop_the_others() -> None:
    drained = threading.Event()

    def broken(_seconds: float) -> None:
        raise RuntimeError("boom")

    on_shutdown(broken, register=lambda _hook: None)
    on_shutdown(lambda _seconds: drained.set(), register=lambda _hook: None)

    shutdown_telemetry(1.0)

    assert drained.is_set()
