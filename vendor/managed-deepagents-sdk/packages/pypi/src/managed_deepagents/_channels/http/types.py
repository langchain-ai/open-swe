from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, NotRequired, TypedDict

if TYPE_CHECKING:
    from langgraph_sdk.schema import Interrupt
    from starlette.requests import Request
    from starlette.responses import Response

    from managed_deepagents._channels.run_input import ChannelRunResult
    from managed_deepagents._channels.types import JsonValue, MessageContent


@dataclass(frozen=True)
class HttpChannelRequest:
    request: Request
    raw_body: bytes


@dataclass(frozen=True)
class ChannelName:
    """The channel an event arrived on, by its name in the project."""

    name: str


class HttpChannelInboundMessage(TypedDict):
    user_id: str
    thread_id: str
    """See ``channel_thread_id`` for one thread per provider conversation."""
    content: NotRequired[MessageContent]
    """What a new run starts with. Without it, the channel's ``input`` supplies it
    once MDA knows the thread takes a new turn."""
    target: JsonValue
    delivery_key: NotRequired[str]
    """The provider's ID for this delivery. A thread runs each delivery once, so
    a redelivered event starts nothing."""
    background: NotRequired[bool]
    """Answer the provider before the channel's ``input`` decides what the run
    starts with, and start it in the background: for input that may take longer
    than the provider waits, such as an event endpoint's. A failure then can't
    fail the delivery, so the provider doesn't redeliver it."""


class HttpChannelCancelMessage(TypedDict):
    """The thread a ``cancel`` result stops, and who to tell."""

    user_id: str
    thread_id: str
    target: JsonValue
    """Where to report the stop."""


class HttpPostedMessage(TypedDict):
    id: str
    url: NotRequired[str]


class _ContentPostInput(TypedDict):
    type: Literal["content"]
    content: MessageContent
    target: JsonValue
    new_thread: NotRequired[bool]


class _NativePostInput(TypedDict):
    type: Literal["native"]
    native: JsonValue
    target: JsonValue
    new_thread: NotRequired[bool]


HttpChannelPostInput = _ContentPostInput | _NativePostInput
HttpPost = Callable[[HttpChannelPostInput], Awaitable[HttpPostedMessage]]
HttpOnError = Callable[[Exception, "JsonValue"], Awaitable[None]]
"""The run a message started failed: tell the sender instead of the default
apology ``post`` sends. Called before ``on_finish``."""


class HttpDelivery(TypedDict):
    """A delivery's thread, for notices and links into LangSmith Studio."""

    thread_id: str
    assistant_id: NotRequired[str]
    """The assistant that runs on the thread, when known."""
    raw_event: NotRequired[JsonValue]
    """The provider event ``parse`` returned, if any."""


HttpRunStatus = Literal["success", "error", "timeout", "interrupted"]
"""How a LangGraph run ended: ``interrupted`` when it was cancelled, by a cancel or
a newer message, or replaced by a newer message's ``rollback``."""

HttpSkipReason = Literal["cancel", "waiting", "ignored", "duplicate", "rejected", "dropped"]
"""Why a delivery started no run:

- ``cancel``: it was a cancel, and MDA cancelled the thread's runs.
- ``waiting``: its thread waits on a decision; ``interrupts`` say on what.
- ``ignored``: its input started nothing, such as a handler that declined.
- ``duplicate``: a redelivery of a delivery the thread already took; that
  delivery's own ``on_finish`` reports its run.
- ``rejected``: its thread was busy, and the channel's ``multitask_strategy`` is
  ``reject``.
- ``dropped``: a cancel dropped it while it waited for its busy thread or for its
  input.
"""


class HttpFinishedRun(TypedDict):
    """The run a message started, and how it ended."""

    id: str
    status: HttpRunStatus


class _RunFinish(TypedDict):
    type: Literal["run"]
    run: HttpFinishedRun
    """The run it started, which ended."""
    interrupts: list[Interrupt]
    """What the thread waits on now, as LangGraph's ``thread.interrupts`` lists
    them: non-empty while a decision is pending, made in Studio."""


class _SkippedFinish(TypedDict):
    type: Literal["skipped"]
    reason: HttpSkipReason
    """Why it started no run."""
    interrupts: list[Interrupt]


class _FailedFinish(TypedDict):
    type: Literal["failed"]
    reason: Literal["start", "retry", "observe"]
    """MDA couldn't start its run after the provider got its answer (``start``,
    final); couldn't start it and answered with an error, so the provider may
    redeliver it as a new delivery (``retry``); or lost track of the run, which
    may still answer (``observe``)."""
    interrupts: list[Interrupt]


HttpFinish = _RunFinish | _SkippedFinish | _FailedFinish
"""How a delivery finished, to switch on by ``type``. Each comes with
``interrupts``, the thread's pending interrupts: what it waits on now.

- ``run``: the run it started ended; ``run["status"]`` says how.
- ``skipped``: it started no run; ``reason`` says why (``HttpSkipReason``).
- ``failed``: MDA couldn't start its run after the provider got its answer
  (``start``, final); couldn't start it and answered the provider with an error,
  so it may redeliver it as a new delivery (``retry``); or lost track of the run
  it started (``observe``), which may still answer.
"""


class _RunFinishedDelivery(HttpDelivery, _RunFinish):
    pass


class _SkippedFinishedDelivery(HttpDelivery, _SkippedFinish):
    pass


class _FailedFinishedDelivery(HttpDelivery, _FailedFinish):
    pass


HttpFinishedDelivery = _RunFinishedDelivery | _SkippedFinishedDelivery | _FailedFinishedDelivery
"""A delivery that is done, with how it finished; see ``HttpFinish`` and
``HttpOnFinish``."""


HttpInput = Callable[
    [HttpChannelInboundMessage, "JsonValue | None"],
    "ChannelRunResult | Awaitable[ChannelRunResult | None] | None",
]
"""What a message without ``content`` starts a run with, asked only once the
thread takes a new turn, never while it waits on an interrupt. ``None`` starts
nothing."""


HttpOnReceived = Callable[["JsonValue", HttpDelivery], Awaitable[None]]
"""A verified message reached its thread, before MDA starts or queues anything
for it. For providers that expect a prompt acknowledgement. Each message that
gets it later gets ``on_finish``. Runs in the background; failures are
logged."""
HttpOnFinish = Callable[["JsonValue", HttpFinishedDelivery], Awaitable[None]]
"""A message is done, once, with how: the run it started ended (``run``), it
started none and why (``skipped``), or MDA couldn't start the run or lost track
of it (``failed``); see ``HttpFinish``. ``interrupts`` are what the thread waits
on now. Runs in the background, after ``on_received`` and ``on_error``;
failures are logged.

It runs in the process that took the delivery, so a restart loses it, and a run
Studio resumes has no delivery. Keep a reconciliation, or set the run's
``webhook``, for anything that must not miss a run."""


class _MessageResultRequired(TypedDict):
    type: Literal["message"]
    message: HttpChannelInboundMessage


class HttpChannelMessageResult(_MessageResultRequired, total=False):
    response: Response
    raw_event: JsonValue


class _IgnoreResultRequired(TypedDict):
    type: Literal["ignore"]


class HttpChannelIgnoreResult(_IgnoreResultRequired, total=False):
    response: Response


class _CancelResultRequired(TypedDict):
    type: Literal["cancel"]
    message: HttpChannelCancelMessage


class HttpChannelCancelResult(_CancelResultRequired, total=False):
    """Stop the work on the message's thread.

    MDA cancels its queued and running runs, keeping their progress, then calls
    ``on_finish``.
    """

    response: Response
    raw_event: JsonValue


HttpChannelParseResult = (
    HttpChannelMessageResult | HttpChannelIgnoreResult | HttpChannelCancelResult
)
VerifyHttpRequest = Callable[[HttpChannelRequest], bool | Awaitable[bool]]
ParseHttpRequest = Callable[
    [HttpChannelRequest], HttpChannelParseResult | Awaitable[HttpChannelParseResult]
]
