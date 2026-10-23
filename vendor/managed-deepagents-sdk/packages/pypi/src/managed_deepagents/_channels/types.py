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


ChannelEvent: TypeAlias = (
    MessageEvent | UserPromptResponseEvent | InterruptResumeEvent | ScheduleEvent
)


@dataclass(frozen=True)
class ChannelInfo:
    name: str
    provider: str
    event: ChannelEvent
    raw_event: JsonValue = None


class PostedChannelMessageRequired(TypedDict):
    id: str


class PostedChannelMessage(PostedChannelMessageRequired, total=False):
    provider_message_id: str
    url: str


class ChannelError(Exception):
    """Typed channel failure with a stable machine-readable code."""

    def __init__(self, code: ChannelErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code


def normalize_channel_destination(
    destination: ChannelDestination | Mapping[str, object],
) -> (
    ChannelDestinationCurrentThread
    | ChannelDestinationProviderThread
    | ChannelDestinationProviderConversation
):
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
