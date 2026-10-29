"""Application-owned run context and prompt-response payload types."""

from typing import Annotated, Literal, NotRequired, TypeAlias

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, JsonValue, with_config
from typing_extensions import TypedDict

#: Key an app sets on the value it passes to ``interrupt()`` so a later answer
#: can be matched back to that interrupt. Carry the same id on the prompt you
#: post.
INTERRUPT_CORRELATION_KEY = "mda_correlation_id"


class PromptResponseRequired(TypedDict):
    provider: str
    action: str


class PromptResponse(PromptResponseRequired, total=False):
    """A user's response to a prompt the app itself posted."""

    value: object
    #: Matched against ``INTERRUPT_CORRELATION_KEY`` on each pending interrupt.
    correlation_id: str


# A TypedDict cannot narrow an inherited field to a literal.
class SlackPromptResponseRequired(TypedDict):
    provider: Literal["slack"]
    action: str


class SlackPromptResponse(SlackPromptResponseRequired, total=False):
    value: object
    correlation_id: str


NormalizedChannelEvent: TypeAlias = SlackPromptResponse


def _nonblank(value: str) -> str:
    if not value.strip():
        raise ValueError("must be nonblank")
    return value


_NonblankString = Annotated[str, AfterValidator(_nonblank)]


def is_managed_key(key: str) -> bool:
    """``mda_*`` keys carry managed channel and schedule routing."""
    return key.startswith("mda_")


_CHANNEL_CONFIG = ConfigDict(
    extra="ignore", strict=True, populate_by_name=True, allow_inf_nan=False
)


@with_config(_CHANNEL_CONFIG)
class ChannelMessageEvent(TypedDict):
    type: Literal["message"]
    messages: list[dict[str, JsonValue]]


@with_config(_CHANNEL_CONFIG)
class ChannelInterruptResumeEvent(TypedDict):
    type: Literal["interrupt_resume"]
    resume: dict[str, JsonValue]


@with_config(_CHANNEL_CONFIG)
class ChannelPromptResponseEvent(TypedDict):
    type: Literal["user_prompt_response"]
    action: _NonblankString
    value: NotRequired[JsonValue]
    correlation_id: NotRequired[_NonblankString]


@with_config(_CHANNEL_CONFIG)
class ChannelScheduleEvent(TypedDict):
    type: Literal["schedule"]
    new_thread: bool


@with_config(_CHANNEL_CONFIG)
class ChannelContext(TypedDict):
    """Serializable routing and event data for a managed channel delivery."""

    name: _NonblankString
    provider: _NonblankString
    source_thread_key: Annotated[_NonblankString, Field(alias="sourceThreadKey")]
    delivery_id: Annotated[_NonblankString, Field(alias="deliveryId")]
    transport: Literal["trigger_server", "direct"]
    target: JsonValue
    raw_event: NotRequired[Annotated[JsonValue, Field(alias="rawEvent")]]
    #: Absent for the channel a thread record names: no message started the run.
    event: NotRequired[
        Annotated[
            ChannelMessageEvent
            | ChannelInterruptResumeEvent
            | ChannelPromptResponseEvent
            | ChannelScheduleEvent,
            Field(discriminator="type"),
        ]
    ]


class ManagedChannelContext(BaseModel):
    """Native LangGraph context schema for authenticated managed channel runs."""

    model_config = _CHANNEL_CONFIG

    channel: ChannelContext


# Application-owned run data for direct API runs.
ManagedRunContext: TypeAlias = dict[str, object] | ManagedChannelContext


__all__ = [
    "INTERRUPT_CORRELATION_KEY",
    "ChannelContext",
    "ManagedChannelContext",
    "ManagedRunContext",
    "NormalizedChannelEvent",
    "PromptResponse",
    "SlackPromptResponse",
]
