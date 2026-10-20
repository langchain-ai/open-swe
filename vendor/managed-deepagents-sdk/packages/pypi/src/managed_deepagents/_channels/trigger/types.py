"""Trigger Server message wire types and envelope validation."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated, Literal, TypeAlias, TypedDict, cast
from uuid import UUID

from langgraph_sdk.schema import Interrupt
from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    SkipValidation,
    TypeAdapter,
    field_validator,
)

TRIGGER_SERVICE_KEY_HEADER = "x-service-key"

TriggerChannelAddress: TypeAlias = dict[str, object]
TriggerChannelRawEvent: TypeAlias = dict[str, object]


class TriggerChannelMessageInput(TypedDict):
    messages: list[dict[str, object]]


class TriggerChannelMessageEventRequired(TypedDict):
    version: Literal["1"]
    type: Literal["message"]
    event_id: str
    delivery_id: str
    created_at: str
    channel_id: str
    source_thread_key: str
    address: TriggerChannelAddress
    input: TriggerChannelMessageInput


class TriggerChannelMessageEvent(TriggerChannelMessageEventRequired, total=False):
    identity_context: str | None
    raw_event: TriggerChannelRawEvent | None


# Keyed LangGraph resume: `{interrupt_id: value}`. Values are opaque
# (`interrupt()` results or HITL `{decisions}`).
TriggerChannelResume: TypeAlias = dict[str, object]


class TriggerChannelInterruptResumeInteraction(TypedDict):
    type: Literal["interrupt_resume"]
    resume: TriggerChannelResume


class TriggerChannelInterruptResumeEventRequired(TypedDict):
    version: Literal["1"]
    type: Literal["interrupt_resume"]
    event_id: str
    delivery_id: str
    created_at: str
    channel_id: str
    source_thread_key: str
    address: TriggerChannelAddress
    interaction: TriggerChannelInterruptResumeInteraction


class TriggerChannelInterruptResumeEvent(TriggerChannelInterruptResumeEventRequired, total=False):
    identity_context: str | None
    raw_event: TriggerChannelRawEvent | None


class TriggerChannelUserPromptResponseInteractionRequired(TypedDict):
    action: str


class TriggerChannelUserPromptResponseInteraction(
    TriggerChannelUserPromptResponseInteractionRequired, total=False
):
    value: object
    correlation_id: str


class TriggerChannelSlackUserPromptResponseEventRequired(TypedDict):
    version: Literal["1"]
    type: Literal["user_prompt_response"]
    provider: Literal["slack"]
    event_id: str
    delivery_id: str
    created_at: str
    channel_id: str
    source_thread_key: str
    address: TriggerChannelAddress
    interaction: TriggerChannelUserPromptResponseInteraction


class TriggerChannelSlackUserPromptResponseEvent(
    TriggerChannelSlackUserPromptResponseEventRequired, total=False
):
    identity_context: str | None
    raw_event: TriggerChannelRawEvent | None


# Add a provider here; do not replace the Slack member.
TriggerChannelUserPromptResponseEvent: TypeAlias = TriggerChannelSlackUserPromptResponseEvent


TriggerChannelEvent = (
    TriggerChannelMessageEvent
    | TriggerChannelInterruptResumeEvent
    | TriggerChannelUserPromptResponseEvent
)


class TriggerChannelActionMessage(TypedDict):
    text: str


class TriggerChannelActionReaction(TypedDict):
    """Emoji to add, and the message to add it to.

    ``emoji`` is a provider short name without colons (Slack: ``eyes``, ``bug``,
    …). ``message_id`` is the inbound event's ``message.id``, not anything on
    ``address``: ``address.thread_id`` is the thread root, which is a different
    message whenever the run was triggered by a reply.
    """

    emoji: str
    message_id: str


class _TriggerChannelActionBaseRequired(TypedDict):
    action_id: str
    address: TriggerChannelAddress


class _TriggerChannelActionBase(_TriggerChannelActionBaseRequired, total=False):
    source_thread_key: str


class TriggerChannelPostAction(_TriggerChannelActionBase):
    """Post the run's reply into the source conversation."""

    type: Literal["post"]
    message: TriggerChannelActionMessage


class TriggerChannelReactAction(_TriggerChannelActionBase):
    """React to the message that triggered the run."""

    type: Literal["react"]
    reaction: TriggerChannelActionReaction


TriggerChannelActionRequest = TriggerChannelPostAction | TriggerChannelReactAction


class TriggerChannelActionResponse(TypedDict):
    id: str


TriggerChannelRuntimeEventType = Literal[
    "run.started",
    "run.completed",
    "run.interrupted",
    "run.failed",
]


#: One pending LangGraph interrupt, forwarded verbatim on `run.interrupted`.
#: `id` is the resume key the Trigger Server sends back in
#: `interaction.resume`; `value` is opaque to MDA. `response_schema`, when the
#: interrupt declares one (per-call tool approvals do), is the JSON schema the
#: answer must match.
TriggerChannelInterrupt = Interrupt


class TriggerChannelRuntimeEventRequestRequired(TypedDict):
    id: str
    type: TriggerChannelRuntimeEventType
    delivery_id: str
    source_thread_key: str
    address: TriggerChannelAddress
    run_thread_id: str
    run_id: str


class TriggerChannelRuntimeEventRequest(TriggerChannelRuntimeEventRequestRequired, total=False):
    # Safe, user-facing status text. Raw runtime exceptions must never be sent.
    message: TriggerChannelActionMessage
    # Present on `run.interrupted`. The Trigger Server cannot read the
    # deployment's threads, so pending interrupts travel on the event itself.
    interrupts: list[TriggerChannelInterrupt]
    # The LangSmith tracing project this deployment writes runs to, so the
    # channel can deep-link a run's trace. An opaque id, never a URL: a URL
    # from here would let the deployment choose where a rendered link points.
    tracer_session_id: str


class TriggerChannelRuntimeEventResponse(TypedDict):
    status: str


def _nonblank(value: str) -> str:
    if not value.strip():
        raise ValueError("must be nonblank")
    return value


_NonBlankString = Annotated[str, AfterValidator(_nonblank)]


def _uuid_string(value: str) -> str:
    UUID(value)
    return value


_UuidString = Annotated[str, AfterValidator(_uuid_string)]


class _WireModel(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)


class _InputModel(_WireModel):
    messages: SkipValidation[list[dict[str, object]]]

    @field_validator("messages", mode="before")
    @classmethod
    def _opaque_messages(cls, value: object) -> object:
        if not isinstance(value, list):
            raise ValueError("channel event input.messages must be a list")
        if any(not isinstance(item, Mapping) for item in value):
            raise ValueError("channel event input.messages items must be objects")
        return value


def _opaque_mapping(value: object, *, field: str) -> object:
    if not isinstance(value, Mapping):
        raise ValueError(f"channel event {field} must be an object")
    return value


class _MessageEventModel(_WireModel):
    version: Literal["1"]
    type: Literal["message"]
    event_id: _NonBlankString
    delivery_id: _NonBlankString
    created_at: _NonBlankString
    channel_id: _UuidString
    source_thread_key: _NonBlankString
    address: SkipValidation[dict[str, object]]
    input: _InputModel
    identity_context: str | None = None
    raw_event: dict[str, object] | None = None

    @field_validator("address", mode="before")
    @classmethod
    def _opaque_address(cls, value: object) -> object:
        return _opaque_mapping(value, field="address")


class _InterruptResumeInteractionModel(_WireModel):
    type: Literal["interrupt_resume"]
    resume: SkipValidation[dict[str, object]]

    @field_validator("resume", mode="before")
    @classmethod
    def _opaque_resume(cls, value: object) -> object:
        return _opaque_mapping(value, field="interaction.resume")


class _InterruptResumeEventModel(_WireModel):
    version: Literal["1"]
    type: Literal["interrupt_resume"]
    event_id: _NonBlankString
    delivery_id: _NonBlankString
    created_at: _NonBlankString
    channel_id: _UuidString
    source_thread_key: _NonBlankString
    address: SkipValidation[dict[str, object]]
    interaction: _InterruptResumeInteractionModel
    identity_context: str | None = None
    raw_event: dict[str, object] | None = None

    @field_validator("address", mode="before")
    @classmethod
    def _opaque_address(cls, value: object) -> object:
        return _opaque_mapping(value, field="address")


class _UserPromptResponseInteractionModel(_WireModel):
    action: _NonBlankString
    value: SkipValidation[object] = None
    correlation_id: _NonBlankString | None = None


class _SlackUserPromptResponseEventModel(_WireModel):
    version: Literal["1"]
    type: Literal["user_prompt_response"]
    provider: Literal["slack"]
    event_id: _NonBlankString
    delivery_id: _NonBlankString
    created_at: _NonBlankString
    channel_id: _UuidString
    source_thread_key: _NonBlankString
    address: SkipValidation[dict[str, object]]
    interaction: _UserPromptResponseInteractionModel
    identity_context: str | None = None
    raw_event: dict[str, object] | None = None

    @field_validator("address", mode="before")
    @classmethod
    def _opaque_address(cls, value: object) -> object:
        return _opaque_mapping(value, field="address")


_USER_PROMPT_RESPONSE_EVENT = Annotated[
    _SlackUserPromptResponseEventModel, Field(discriminator="provider")
]

_EVENT_ADAPTER = TypeAdapter(
    Annotated[
        _MessageEventModel | _InterruptResumeEventModel | _USER_PROMPT_RESPONSE_EVENT,
        Field(discriminator="type"),
    ]
)


def parse_trigger_channel_event(value: object) -> TriggerChannelEvent:
    """Validate a v1 message envelope without replacing its opaque mappings."""
    _EVENT_ADAPTER.validate_python(value, strict=True)
    return cast("TriggerChannelEvent", value)


__all__ = [
    "TRIGGER_SERVICE_KEY_HEADER",
    "TriggerChannelActionRequest",
    "TriggerChannelActionResponse",
    "TriggerChannelAddress",
    "TriggerChannelEvent",
    "TriggerChannelInterrupt",
    "TriggerChannelInterruptResumeEvent",
    "TriggerChannelMessageEvent",
    "TriggerChannelPostAction",
    "TriggerChannelRawEvent",
    "TriggerChannelReactAction",
    "TriggerChannelResume",
    "TriggerChannelRuntimeEventRequest",
    "TriggerChannelRuntimeEventResponse",
    "TriggerChannelRuntimeEventType",
    "TriggerChannelSlackUserPromptResponseEvent",
    "TriggerChannelUserPromptResponseEvent",
    "TriggerChannelUserPromptResponseInteraction",
    "parse_trigger_channel_event",
]
