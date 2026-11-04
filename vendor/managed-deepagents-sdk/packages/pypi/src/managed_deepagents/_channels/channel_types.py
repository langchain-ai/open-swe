"""The shapes every provider channel shares.

A handler's thread and what it returns are the same for ``channels.github``,
``channels.linear`` and ``channels.slack``; each channel's own types build on
these.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Protocol, TypeAlias, runtime_checkable

from typing_extensions import TypeVar

from managed_deepagents._channels.reply import ChannelReplyResult
from managed_deepagents._channels.run_input import ChannelRunResult

if TYPE_CHECKING:
    from langgraph_sdk.schema import ThreadState

    from managed_deepagents._channels.http.types import ChannelName

ChannelEventResult: TypeAlias = ChannelRunResult | ChannelReplyResult | None
"""What an event handler returns: a run's input, as ``agent.invoke`` takes it (a
string, a message such as ``HumanMessage(...)``, or state such as
``{"messages": [...]}``), one run on the event's conversation; or a list of runs
with options, such as ``[{"input": ..., "context": ..., "metadata": ...}]``.
``None`` starts nothing. ``reply(message)`` starts nothing too, and posts
``message`` where the event came from."""


@runtime_checkable
class ChannelThread(Protocol):
    """The conversation's thread an event belongs to.

    A handler runs before any run exists, so this is not a run's runtime. Every
    channel's thread (``GitHubChannelThread``, ``LinearChannelThread``,
    ``SlackChannelThread``) has these.
    """

    @property
    def id(self) -> str:
        """The thread a run the handler starts runs on."""
        ...

    @property
    def channel(self) -> ChannelName:
        """The channel the event came through."""
        ...

    async def get_state(self) -> ThreadState | None:
        """The thread's state; ``None`` when no run started on it yet."""
        ...


_Event = TypeVar("_Event")
_Thread = TypeVar("_Thread", bound=ChannelThread, default=ChannelThread)

ChannelEventCallback: TypeAlias = Callable[
    [_Event, _Thread],
    ChannelEventResult | Awaitable[ChannelEventResult],
]
"""A handler: decides what an event starts, given the event and its thread."""


class ReadsThreadState:
    """``get_state`` for a channel's thread dataclass, which declares the
    ``_get_state`` reader field the channel fills while a handler decides."""

    __slots__ = ()

    _get_state: Callable[[], Awaitable[ThreadState | None]] | None

    async def get_state(self) -> ThreadState | None:
        """The thread's state, as Agent Server's ``threads.get_state`` reads it:
        its ``values`` (such as ``messages``) as its last run left them, ``next``,
        ``tasks`` and ``interrupts``; ``None`` when no run started on it yet.
        Read as the conversation's own runs read it. Only a handler deciding an
        event can read it.
        """
        if self._get_state is None:
            raise RuntimeError("only a handler deciding an event can read its thread's state")
        return await self._get_state()


__all__ = ["ChannelEventCallback", "ChannelEventResult", "ChannelThread", "ReadsThreadState"]
