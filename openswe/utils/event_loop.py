"""Open SWE runs on exactly one event loop per process.

Sandbox state is cached per thread in a process-global registry and reused by
later runs. Isolated queue loops hand each worker its own loop, so anything
loop-affine a run leaves in that registry is unusable from the next run's loop
and strands the thread permanently.

The same loop serves every request, so one request running seconds of plain
Python freezes all the others; ``watch_for_stalls`` logs what it was running.
"""

import asyncio
import logging
import os
import sys
import threading
import time
import traceback
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Final

from openswe.config import ENV

logger = logging.getLogger(__name__)

ISOLATED_LOOPS_ENV = "BG_JOB_ISOLATED_LOOPS"

# Short enough that a stall just past the threshold is still caught.
_PING_INTERVAL_SECONDS: Final = 0.1
_UNRECOVERED_REPORT_FACTOR: Final = 10
_LOG_RECORD_FIELDS: Final = frozenset(vars(logging.makeLogRecord({})))


def pin_single_event_loop() -> None:
    """Force the queue to run every job on the server's loop.

    Raises if the setting cannot be forced: running with isolated loops is worse
    than not booting, because it fails later as a permanently unusable thread.
    """
    requested = ENV.BG_JOB_ISOLATED_LOOPS.get_bool()
    # Covers the server reading its config after us; importing it here instead
    # would drag in a module that demands DATABASE_URI just to be loaded.
    os.environ[ISOLATED_LOOPS_ENV] = "false"

    langgraph_config = sys.modules.get("langgraph_api.config")
    if langgraph_config is None:
        return

    if not hasattr(langgraph_config, ISOLATED_LOOPS_ENV):
        msg = (
            f"langgraph_api.config has no {ISOLATED_LOOPS_ENV}. The queue's loop model "
            "changed, so Open SWE can no longer guarantee one event loop per process."
        )
        raise RuntimeError(msg)

    setattr(langgraph_config, ISOLATED_LOOPS_ENV, False)
    if getattr(langgraph_config, ISOLATED_LOOPS_ENV):
        msg = f"Could not force {ISOLATED_LOOPS_ENV} off"
        raise RuntimeError(msg)

    if requested:
        logger.warning(
            "%s was set; forcing it off. Open SWE requires one event loop per process.",
            ISOLATED_LOOPS_ENV,
        )


@dataclass(frozen=True, slots=True)
class _Stall:
    """What the loop was running when it first went quiet for too long."""

    stack: str
    task: str | None
    serving: Mapping[str, str | int]

    def log(self, seconds: float, *, recovered: bool) -> None:
        logger.warning(
            "Event loop stalled",
            extra={
                "stall_ms": round(seconds * 1000),
                "stall_recovered": recovered,
                "loop_stack": self.stack,
                "loop_task": self.task,
                **self.serving,
            },
        )


class StallWatchdog:
    """Pings the loop from a thread of its own and logs each stall when it ends.

    A stall that outlasts ten thresholds is also logged while it is still going on.

    Construct it on the loop's thread: that is the thread whose stack it samples.
    """

    def __init__(self, loop: asyncio.AbstractEventLoop, threshold_seconds: float) -> None:
        self._loop = loop
        self._loop_thread = threading.get_ident()
        self._threshold = threshold_seconds
        self._logging_context = _logging_context_var()
        self._stopped = threading.Event()
        self._thread = threading.Thread(target=self._watch, name="event-loop-watchdog", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stopped.set()
        self._thread.join()

    def _watch(self) -> None:
        while not self._stopped.wait(_PING_INTERVAL_SECONDS):
            answered = threading.Event()
            sent = time.monotonic()
            try:
                self._loop.call_soon_threadsafe(answered.set)
            except RuntimeError:
                logger.debug("Event loop closed; stall watchdog exiting", exc_info=True)
                return
            # A ping left waiting by `stop()` itself is shutdown, not a stall.
            if answered.wait(self._threshold) or self._stopped.is_set():
                continue
            # Sampled while the loop is still stuck, so the stack shows what holds it.
            stall = self._sample()
            reported = False
            while not answered.wait(self._threshold):
                if self._stopped.is_set():
                    return
                stalled = time.monotonic() - sent
                # The pod may be restarted before a stall this long ends, if it ever does.
                if not reported and stalled >= self._threshold * _UNRECOVERED_REPORT_FACTOR:
                    stall.log(stalled, recovered=False)
                    reported = True
            stall.log(time.monotonic() - sent, recovered=True)

    def _sample(self) -> _Stall:
        frame = sys._current_frames().get(self._loop_thread)  # noqa: SLF001
        stack = "".join(traceback.format_stack(frame)) if frame is not None else ""
        task = asyncio.current_task(self._loop)
        if task is None:
            return _Stall(stack=stack, task=None, serving={})
        coro = task.get_coro()
        name = task.get_name() if coro is None else f"{task.get_name()} {coro.__qualname__}"
        serving: Mapping[str, object] | None = None
        if self._logging_context is not None:
            serving = task.get_context().get(self._logging_context)
        return _Stall(stack=stack, task=name, serving=_loggable(serving or {}))


def _logging_context_var() -> ContextVar[dict[str, object] | None] | None:
    """The server's per-request logging fields (request, thread, run, endpoint), if it runs here."""
    try:
        from langgraph_api.logging import worker_config
    except ImportError:
        logger.debug("No server logging context to attribute stalls to", exc_info=True)
        return None
    return worker_config


def _loggable(fields: Mapping[str, object]) -> dict[str, str | int]:
    return {
        key: value
        for key, value in fields.items()
        if isinstance(value, str | int) and key not in _LOG_RECORD_FIELDS
    }


@contextmanager
def watch_for_stalls() -> Iterator[None]:
    """Log every stretch the running loop goes unresponsive past ``EVENT_LOOP_STALL_MS``."""
    threshold_ms = ENV.EVENT_LOOP_STALL_MS.get_int(500)
    if threshold_ms <= 0:
        yield
        return
    watchdog = StallWatchdog(asyncio.get_running_loop(), threshold_ms / 1000)
    watchdog.start()
    try:
        yield
    finally:
        watchdog.stop()
