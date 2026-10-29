"""Async resources owned by one managed graph invocation."""

from collections.abc import Awaitable, Callable
from contextlib import AsyncExitStack
from contextvars import ContextVar

_current_resources: ContextVar[AsyncExitStack | None] = ContextVar(
    "managed_run_resources", default=None
)


def on_run_exit(cleanup: Callable[[], Awaitable[None]]) -> None:
    """Run cleanup after this invocation's graph tasks have stopped."""
    resources = _current_resources.get()
    if resources is None:
        raise RuntimeError("Run resources require an active managed invocation")
    resources.push_async_callback(cleanup)
