"""The thread state a provider channel's ``input`` may read while it decides an event."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from contextvars import ContextVar, Token
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from langgraph_sdk.schema import ThreadState

ReadState = Callable[[], Awaitable["ThreadState | None"]]

_READ_STATE: ContextVar[ReadState | None] = ContextVar("mda_channel_input_state", default=None)


def input_state_reader() -> ReadState | None:
    """The conversation's state reader while the channel's ``input`` decides; else ``None``."""
    return _READ_STATE.get()


def set_input_state_reader(read: ReadState | None) -> Token[ReadState | None]:
    """Set the reader for the input about to be asked; reset it with the returned token."""
    return _READ_STATE.set(read)


def reset_input_state_reader(token: Token[ReadState | None]) -> None:
    """Restore the reader that was set before ``set_input_state_reader``."""
    _READ_STATE.reset(token)
