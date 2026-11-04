"""Channel-neutral authoring and runtime contracts.

Provider-specific fields belong in a validated ``native`` payload or the
managed adapter — never on these core types.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal, NotRequired, TypeAlias, Union

from langchain_core.messages import ContentBlock
from typing_extensions import ReadOnly, TypeAliasType, TypedDict

JsonValue = TypeAliasType(
    "JsonValue",
    Union[bool, int, float, str, Sequence["JsonValue"], Mapping[str, "JsonValue"], None],  # noqa: UP007
)
"""A JSON value. A recursive alias pydantic builds, so a TypedDict that uses it,
such as ``EndpointAnswer``, validates as it is typed."""

ChannelErrorCode = Literal[
    "channel_invalid_signature",
    "channel_replay_rejected",
    "channel_installation_not_found",
    "channel_event_filtered",
    "channel_identity_unresolvable",
    "channel_destination_forbidden",
    "channel_invocation_rate_limited",
    "channel_run_failed",
    "channel_provider_rate_limited",
    "channel_provider_rejected",
]


class ChannelAddressRequired(TypedDict):
    conversation_id: str


class ChannelAddress(ChannelAddressRequired, total=False):
    thread_id: str


class ChannelDestinationCurrentThread(TypedDict):
    type: Literal["current_thread"]


class ChannelDestinationProviderThread(TypedDict):
    type: Literal["provider_thread"]
    conversation_id: str
    thread_id: str


class ChannelDestinationProviderConversation(TypedDict):
    type: Literal["provider_conversation"]
    conversation_id: str


ChannelDestination = (
    ChannelDestinationCurrentThread
    | ChannelDestinationProviderThread
    | ChannelDestinationProviderConversation
)
"""Where a schedule's deprecated ``deliver_to`` posts; ``auto_post`` takes a
``ChannelTarget`` instead."""


MessageContent: TypeAlias = str | list[ContentBlock]


class ChannelContentMessage(TypedDict):
    type: Literal["content"]
    content: MessageContent


class ChannelNativeMessage(TypedDict):
    type: Literal["native"]
    native: JsonValue


ChannelMessage: TypeAlias = ChannelContentMessage | ChannelNativeMessage
OutboundMessage: TypeAlias = ChannelMessage


class MessageEvent(TypedDict):
    type: ReadOnly[Literal["message"]]
    messages: ReadOnly[Sequence[Mapping[str, object]]]


class UserPromptResponseEvent(TypedDict):
    type: ReadOnly[Literal["user_prompt_response"]]
    action: ReadOnly[str]
    value: NotRequired[ReadOnly[object]]
    correlation_id: NotRequired[ReadOnly[str]]


class InterruptResumeEvent(TypedDict):
    type: ReadOnly[Literal["interrupt_resume"]]
    resume: ReadOnly[Mapping[str, object]]


class ScheduleEvent(TypedDict):
    type: ReadOnly[Literal["schedule"]]
    new_thread: ReadOnly[bool]


ChannelReply: TypeAlias = Literal["auto", "none"]
"""Whether a channel posts the run's final answer to the conversation:
``"auto"`` (the default) posts the agent's last reply once the run succeeds,
and ``"none"`` leaves replying to the agent, through ``runtime.channel.post``
or its own tools. Either way the channel keeps its other updates, such as
Slack's status and reactions.

Experimental: this may change in a minor release.
"""

CHANNEL_REPLIES: frozenset[ChannelReply] = frozenset({"auto", "none"})
"""Every ``ChannelReply``, for channels that check one."""

ChannelEvent: TypeAlias = (
    MessageEvent | UserPromptResponseEvent | InterruptResumeEvent | ScheduleEvent
)


@dataclass(frozen=True)
class ChannelInfo:
    name: str
    provider: str
    #: The event of the message that started this run, or the schedule event of a
    #: scheduled run; ``None`` for a channel read from the thread record.
    event: ChannelEvent | None
    #: The provider's payload, in the run its message started only.
    raw_event: JsonValue = None
    #: Where the channel posts: the provider conversation that ``post`` and the
    #: run's answers go to, which is the conversation the thread came from, in
    #: every run of the thread. ``None`` for a provider without a typed target,
    #: such as ``channels.http()``.
    target: ChannelTarget | None = None


@dataclass(frozen=True)
class SlackChannelTarget:
    """A Slack conversation: a channel, DM or group, and its thread."""

    #: The conversation id (``C…``, ``D…`` or ``G…``), Slack's ``channel``.
    conversation_id: str
    #: The thread's root message, Slack's ``thread_ts``.
    thread_ts: str | None = None
    provider: Literal["slack"] = "slack"
    #: The channel's name, needed only to tell apart two channels of one
    #: provider. ``None`` is the channel the target is used with.
    channel: str | None = None


@dataclass(frozen=True)
class GitHubChannelTarget:
    """A GitHub issue or pull request."""

    #: ``owner/name``.
    repository: str
    #: The issue or pull request number.
    number: int
    provider: Literal["github"] = "github"
    #: The channel's name; see ``SlackChannelTarget.channel``.
    channel: str | None = None


@dataclass(frozen=True)
class LinearChannelTarget:
    """A Linear issue, and the comment thread or agent session on it."""

    organization_id: str
    issue_id: str
    #: The issue's identifier, such as ``ENG-123``.
    identifier: str | None = None
    #: The root comment of the thread replies go to.
    comment_id: str | None = None
    #: The agent session replies go to.
    session_id: str | None = None
    provider: Literal["linear"] = "linear"
    #: The channel's name; see ``SlackChannelTarget.channel``.
    channel: str | None = None


ChannelTarget: TypeAlias = SlackChannelTarget | GitHubChannelTarget | LinearChannelTarget
"""Where a channel posts, by provider; narrow it on ``provider`` or its class."""


class PostedChannelMessageRequired(TypedDict):
    id: str


class PostedChannelMessage(PostedChannelMessageRequired, total=False):
    provider_message_id: str
    url: str


class PostedChannelThread(PostedChannelMessage):
    """A post that started a new provider thread.

    Experimental: this may change in a minor release.
    """

    thread_id: str
    """The LangGraph thread replies in the new provider thread run on."""


class ChannelError(Exception):
    """Typed channel failure with a stable machine-readable code."""

    def __init__(self, code: ChannelErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code


def normalize_channel_destination(
    destination: ChannelDestination | Mapping[str, object],
) -> ChannelDestination:
    """Narrow a destination to its discriminant + required address fields."""
    dest_type = destination.get("type")
    if dest_type == "current_thread":
        return {"type": "current_thread"}
    if dest_type == "provider_thread":
        conversation_id = destination.get("conversation_id")
        thread_id = destination.get("thread_id")
        if not isinstance(conversation_id, str) or not isinstance(thread_id, str):
            raise ValueError("provider_thread destination requires conversation_id and thread_id")
        return {
            "type": "provider_thread",
            "conversation_id": conversation_id,
            "thread_id": thread_id,
        }
    if dest_type == "provider_conversation":
        conversation_id = destination.get("conversation_id")
        if not isinstance(conversation_id, str):
            raise ValueError("provider_conversation destination requires conversation_id")
        return {"type": "provider_conversation", "conversation_id": conversation_id}
    raise ValueError(f"unsupported channel destination type: {dest_type!r}")


__all__ = [
    "ChannelAddress",
    "ChannelContentMessage",
    "ChannelDestination",
    "ChannelError",
    "ChannelErrorCode",
    "ChannelEvent",
    "ChannelMessage",
    "ChannelNativeMessage",
    "JsonValue",
    "PostedChannelMessage",
    "normalize_channel_destination",
]
