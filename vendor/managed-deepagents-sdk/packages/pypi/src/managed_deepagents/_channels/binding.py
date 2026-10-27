"""Serializable current-delivery authority installed by managed ingress."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from contextvars import ContextVar
from typing import TYPE_CHECKING, Literal, NotRequired, TypedDict, cast

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError
from pydantic import JsonValue as PydanticJsonValue

from managed_deepagents._channels.messaging_registry import (
    channel_provider_for_name,
    messaging_transport_for_name,
)
from managed_deepagents._channels.runtime import (
    ChannelMessagingAdapter,
    RuntimeChannel,
    channel_info,
    create_runtime_channel,
    is_json_value,
    json_copy,
)
from managed_deepagents._identity_runtime import _field
from managed_deepagents.run_context import ChannelContext

if TYPE_CHECKING:
    from managed_deepagents._channels.run_input import ChannelRunResult
    from managed_deepagents._channels.types import (
        ChannelEvent,
        ChannelInfo,
        ChannelMessage,
        JsonValue,
        PostedChannelThread,
    )
    from managed_deepagents._thread_record import StoredChannel

SLACK_BOT_TOKEN_ENV = "SLACK_BOT_TOKEN"  # noqa: S105 - environment variable name


class ChannelBinding(TypedDict):
    name: str
    provider: str
    source_thread_key: str
    delivery_id: str
    transport: Literal["trigger_server", "direct"]
    target: JsonValue
    #: Absent for a binding read from the thread record: no message started the run.
    event: NotRequired[ChannelEvent]
    raw_event: NotRequired[JsonValue]


def binding_with_messages(
    binding: ChannelBinding, messages: Sequence[Mapping[str, JsonValue]]
) -> ChannelBinding:
    """The binding whose message event holds a run's own input.

    It replaces the delivery's messages, such as with a handler's.
    """
    return cast(
        "ChannelBinding",
        {**binding, "event": {"type": "message", "messages": [dict(m) for m in messages]}},
    )


_current_channel: ContextVar[ChannelBinding | None] = ContextVar("mda_channel", default=None)
#: Cron routing split out of a managed run's context.
_current_routing: ContextVar[Mapping[str, object] | None] = ContextVar("mda_routing", default=None)


class _TriggerTargetModel(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)
    channel_id: str = Field(pattern=r"\S")
    address: dict[str, PydanticJsonValue]
    source_thread_key: str = Field(pattern=r"\S")


_CHANNEL_CONTEXT_ADAPTER = TypeAdapter(ChannelContext)


def resolve_channel_binding(
    context: object, configurable: Mapping[str, object] | None
) -> ChannelBinding | None:
    """Validate delivery context against the authenticated source and declared channel."""
    raw = _field(context, "mda_channel")
    if raw is None:
        raw = _current_channel.get()
    if not isinstance(configurable, Mapping) or not is_json_value(raw):
        return None
    try:
        binding = cast("ChannelBinding", _CHANNEL_CONTEXT_ADAPTER.validate_python(json_copy(raw)))
    except (ValueError, TypeError):
        return None
    user = configurable.get("langgraph_auth_user")
    if (
        _field(user, "mda_source_provider") != binding["provider"]
        or _field(user, "mda_source_thread_id") != binding["source_thread_key"]
        or not _is_declared_route(binding)
    ):
        return None
    return binding


def _is_declared_route(binding: ChannelBinding) -> bool:
    """Whether a binding names a channel of this deployment and a well-formed route."""
    if channel_provider_for_name(binding["name"]) != binding["provider"]:
        return False
    if binding["transport"] == "trigger_server":
        try:
            target = _TriggerTargetModel.model_validate(binding["target"])
        except ValidationError:
            return False
        if target.source_thread_key != binding["source_thread_key"]:
            return False
    return True


def origin_binding(
    channel: StoredChannel, event: ChannelEvent | None = None
) -> ChannelBinding | None:
    """The binding a thread record's origin was stored from, if this deployment declares it.

    Not checked against the caller's source: only managed ingress writes the
    record (the runtime's auth strips it from every other write), and agent
    code is trusted.
    """
    from managed_deepagents._thread_record import target_from_origin  # noqa: PLC0415 - cycle

    binding: ChannelBinding = {
        "name": channel.name,
        "provider": channel.provider,
        "source_thread_key": channel.source_thread_key,
        "delivery_id": channel.delivery_id,
        "transport": channel.transport,
        "target": target_from_origin(channel),
    }
    if event is not None:
        binding["event"] = event
    return binding if _is_declared_route(binding) else None


def resolve_channel_info(
    context: object, configurable: Mapping[str, object] | None
) -> ChannelInfo | None:
    binding = resolve_channel_binding(context, configurable)
    if binding is None:
        return None
    return channel_info(
        name=binding["name"],
        provider=binding["provider"],
        event=binding.get("event"),
        raw_event=binding.get("raw_event"),
    )


def resolve_runtime_channel(
    context: object,
    configurable: Mapping[str, object] | None,
    transport_factory: Callable[[str], object | None] | None = None,
) -> RuntimeChannel | None:
    binding = resolve_channel_binding(context, configurable)
    if binding is None:
        return None
    return runtime_channel(binding, transport_factory)


def runtime_channel(
    binding: ChannelBinding, transport_factory: Callable[[str], object | None] | None = None
) -> RuntimeChannel:
    """A binding's runtime facts, with ``post`` bound to its route."""
    transport = None
    start_thread = None
    if binding["transport"] == "trigger_server":
        from managed_deepagents._channels.trigger.client import (  # noqa: PLC0415 - lazy transport
            create_trigger_client,
        )
        from managed_deepagents._channels.trigger.conversation import (  # noqa: PLC0415 - lazy transport
            start_trigger_conversation,
        )
        from managed_deepagents._channels.trigger.transport import (  # noqa: PLC0415 - lazy transport
            TriggerTransport,
        )

        client = create_trigger_client()
        if client is not None:
            transport = TriggerTransport(client)

            async def start_thread(
                message: ChannelMessage, run: ChannelRunResult | None
            ) -> PostedChannelThread:
                return await start_trigger_conversation(client, binding, message, run)
    else:
        transport = messaging_transport_for_name(binding["name"])
        if transport is None and transport_factory is not None:
            transport = transport_factory(binding["provider"])
    return create_runtime_channel(
        name=binding["name"],
        provider=binding["provider"],
        event=binding.get("event"),
        raw_event=binding.get("raw_event"),
        target=binding["target"],
        transport=cast("ChannelMessagingAdapter | None", transport),
        start_thread=start_thread,
    )


def resolve_channel_provider(configurable: Mapping[str, object] | None) -> str | None:
    """Trusted ingress source for telemetry; direct HTTP callers have no channel stamp."""
    if not isinstance(configurable, Mapping):
        return None
    user = configurable.get("langgraph_auth_user")
    if isinstance(user, Mapping):
        provider = user.get("mda_source_provider", "http")
    elif hasattr(user, "identity"):
        # Agent Server wraps auth results in an attribute-based ProxyUser.
        provider = getattr(user, "mda_source_provider", "http")
    else:
        return None
    return provider if isinstance(provider, str) else None
