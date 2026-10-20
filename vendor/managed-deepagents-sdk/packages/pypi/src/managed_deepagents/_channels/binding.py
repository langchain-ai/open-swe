"""Serializable current-delivery authority installed by managed ingress."""

from __future__ import annotations

from collections.abc import Callable, Mapping
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
    from managed_deepagents._channels.types import ChannelEvent, ChannelInfo, JsonValue

SLACK_BOT_TOKEN_ENV = "SLACK_BOT_TOKEN"  # noqa: S105 - environment variable name


class ChannelBinding(TypedDict):
    name: str
    provider: str
    source_thread_key: str
    delivery_id: str
    transport: Literal["trigger_server", "direct"]
    target: JsonValue
    event: ChannelEvent
    raw_event: NotRequired[JsonValue]


_current_channel: ContextVar[ChannelBinding | None] = ContextVar("mda_channel", default=None)
#: Cron routing split out of a managed run's context.
_current_routing: ContextVar[Mapping[str, object] | None] = ContextVar("mda_routing", default=None)
#: The lead's channel in an async subagent run, for `runtime.channel` only.
_lead_channel: ContextVar[ChannelBinding | None] = ContextVar("mda_lead_channel", default=None)


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
        parsed = _CHANNEL_CONTEXT_ADAPTER.validate_python(json_copy(raw))
    except (ValueError, TypeError):
        return None
    user = configurable.get("langgraph_auth_user")
    if (
        _field(user, "mda_source_provider") != parsed["provider"]
        or _field(user, "mda_source_thread_id") != parsed["source_thread_key"]
        or channel_provider_for_name(parsed["name"]) != parsed["provider"]
    ):
        return None
    if parsed["transport"] == "trigger_server":
        try:
            target = _TriggerTargetModel.model_validate(parsed["target"])
        except ValidationError:
            return None
        if target.source_thread_key != parsed["source_thread_key"]:
            return None
    return cast("ChannelBinding", parsed)


def resolve_channel_info(
    context: object, configurable: Mapping[str, object] | None
) -> ChannelInfo | None:
    binding = resolve_channel_binding(context, configurable)
    if binding is None:
        return None
    return channel_info(
        name=binding["name"],
        provider=binding["provider"],
        event=binding["event"],
        raw_event=binding.get("raw_event"),
    )


def resolve_runtime_channel(
    context: object,
    configurable: Mapping[str, object] | None,
    transport_factory: Callable[[str], object | None] | None = None,
    *,
    include_lead: bool = False,
) -> RuntimeChannel | None:
    binding = resolve_channel_binding(context, configurable)
    if binding is None and include_lead:
        binding = _lead_channel.get()
    if binding is None:
        return None
    transport = None
    if binding["transport"] == "trigger_server":
        from managed_deepagents._channels.trigger.client import (  # noqa: PLC0415 - lazy transport
            create_trigger_client,
        )
        from managed_deepagents._channels.trigger.transport import (  # noqa: PLC0415 - lazy transport
            TriggerTransport,
        )

        client = create_trigger_client()
        if client is not None:
            transport = TriggerTransport(client)
    else:
        transport = messaging_transport_for_name(binding["name"])
        if transport is None and transport_factory is not None:
            transport = transport_factory(binding["provider"])
    return create_runtime_channel(
        name=binding["name"],
        provider=binding["provider"],
        event=binding["event"],
        raw_event=binding.get("raw_event"),
        target=binding["target"],
        transport=cast("ChannelMessagingAdapter | None", transport),
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
