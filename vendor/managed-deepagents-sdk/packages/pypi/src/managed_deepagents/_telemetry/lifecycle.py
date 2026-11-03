"""Process shutdown for the telemetry sinks.

Each sink registers a drain; at interpreter exit they run in parallel under one
shared deadline. There is no signal handler: uvicorn already turns SIGTERM into
a clean exit.
"""

from __future__ import annotations

import atexit
import time
from collections.abc import Callable
from contextlib import suppress
from threading import Lock, Thread
from typing import Final

#: How long the drains get. Kubernetes allows 30s between SIGTERM and SIGKILL
#: by default (``terminationGracePeriodSeconds``), and the server's own drain
#: uses part of it; 5s fits alongside and matches Fluent Bit's default
#: ``Grace``.
SHUTDOWN_DEADLINE_SECONDS: Final = 5.0

#: Sends what a sink holds, giving up after the given seconds.
Drain = Callable[[float], None]

_drains: list[Drain] = []
_hooked = False
_lock = Lock()


def on_shutdown(drain: Drain, *, register: Callable[..., object] = atexit.register) -> None:
    """Run ``drain`` at exit. The first registration installs the hook."""
    global _hooked  # noqa: PLW0603 - one process-wide hook
    with _lock:
        _drains.append(drain)
        if not _hooked:
            _hooked = True
            register(shutdown_telemetry)


def shutdown_telemetry(deadline_seconds: float = SHUTDOWN_DEADLINE_SECONDS) -> None:
    """Drain every registered sink in parallel, returning by the deadline."""
    with _lock:
        drains = list(_drains)
    threads = [
        Thread(target=_drain, args=(drain, deadline_seconds), name="mda-shutdown", daemon=True)
        for drain in drains
    ]
    end = time.monotonic() + deadline_seconds
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=max(end - time.monotonic(), 0))


def _drain(drain: Drain, deadline_seconds: float) -> None:
    # One sink's failure must not stop the others.
    with suppress(Exception):
        drain(deadline_seconds)


def reset_shutdown_for_tests() -> None:
    """Forget registrations and the hook between tests."""
    global _hooked  # noqa: PLW0603 - mirrors the process-wide hook
    with _lock:
        _drains.clear()
        _hooked = False
