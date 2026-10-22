"""Await helpers for sync runtime entrypoints that call async backends."""

from __future__ import annotations

import asyncio
import concurrent.futures
import inspect
import threading
from collections.abc import Awaitable, Coroutine
from typing import Any, TypeVar, cast, overload

_ResultT = TypeVar("_ResultT")
_executor: concurrent.futures.ThreadPoolExecutor | None = None
_executor_lock = threading.Lock()


def _shared_executor() -> concurrent.futures.ThreadPoolExecutor:
    """Process-wide pool for bridging sync callers to async / blocking work."""
    global _executor  # noqa: PLW0603
    with _executor_lock:
        if _executor is None:
            _executor = concurrent.futures.ThreadPoolExecutor(
                max_workers=4,
                thread_name_prefix="mda-async-util",
            )
        return _executor


@overload
def await_if_needed(value: Awaitable[_ResultT]) -> _ResultT: ...


@overload
def await_if_needed(value: _ResultT) -> _ResultT: ...


def await_if_needed(value: _ResultT | Awaitable[_ResultT]) -> _ResultT:
    """Resolve a value that may be awaitable.

    The compiled Python entry runs synchronously, so if a provider returns a
    coroutine we drive it to completion on a private event loop.
    """
    if not inspect.isawaitable(value):
        return cast("_ResultT", value)

    # ``asyncio.run`` requires a Coroutine; providers may return any Awaitable.
    coro = cast("Coroutine[Any, Any, _ResultT]", value)

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    # Already inside a running loop: run the coroutine on a dedicated loop in a
    # worker thread so we don't deadlock the caller's loop.
    return _shared_executor().submit(lambda: asyncio.run(coro)).result()
