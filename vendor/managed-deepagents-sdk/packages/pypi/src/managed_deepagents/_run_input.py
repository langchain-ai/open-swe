"""Config defaults for native LangGraph execution."""

from collections.abc import AsyncIterator, Iterator, Mapping
from typing import Any, cast

from langchain_core.runnables.config import RunnableConfig
from langgraph._internal._config import ensure_config, patch_configurable
from langgraph._internal._constants import CONFIG_KEY_RUNTIME
from langgraph.graph.state import CompiledStateGraph
from langgraph.runtime import Runtime

from managed_deepagents._channels.binding import (
    ChannelBinding,
    _current_channel,
    _current_routing,
    resolve_channel_binding,
)
from managed_deepagents._identity_runtime import _field
from managed_deepagents.run_context import is_managed_key


def graph_default_config(config: Mapping[str, object] | None) -> RunnableConfig:
    """Keep SDK factory runtimes out of config sent to the hosted checkpointer.

    Agent Server supplies its ServerRuntime separately to the factory. It lacks
    the context/previous fields required by the gRPC checkpointer's native
    Runtime serializer. LangGraph receives execution context through its native API.
    """
    prepared = dict(config or {})
    configurable = dict(cast("Mapping[str, object]", prepared.get("configurable") or {}))
    if CONFIG_KEY_RUNTIME in configurable and not isinstance(
        configurable[CONFIG_KEY_RUNTIME], Runtime
    ):
        configurable.pop(CONFIG_KEY_RUNTIME)
        prepared["configurable"] = configurable
    return cast("RunnableConfig", prepared)


class ManagedCompiledAgent(CompiledStateGraph[Any, Any, Any, Any]):
    """Remove ingress transport before native context conversion, on every execution."""

    def stream(
        self,
        input: Any,
        config: RunnableConfig | None = None,
        *,
        context: Any = None,
        **kwargs: Any,
    ) -> Iterator[Any]:
        binding, context, routing = execution_context(context, self.config, config)
        config = execution_config(self.config, config)
        iterator = super().stream(input, config, context=context, **kwargs)
        try:
            while True:
                token = _current_channel.set(binding)
                routing_token = _current_routing.set(routing)
                try:
                    item = next(iterator)
                except StopIteration:
                    return
                finally:
                    _current_routing.reset(routing_token)
                    _current_channel.reset(token)
                yield item
        finally:
            iterator.close()

    async def astream(
        self,
        input: Any,
        config: RunnableConfig | None = None,
        *,
        context: Any = None,
        **kwargs: Any,
    ) -> AsyncIterator[Any]:
        binding, context, routing = execution_context(context, self.config, config)
        config = execution_config(self.config, config)
        iterator = super().astream(input, config, context=context, **kwargs)
        try:
            while True:
                token = _current_channel.set(binding)
                routing_token = _current_routing.set(routing)
                try:
                    item = await anext(iterator)
                except StopAsyncIteration:
                    return
                finally:
                    _current_routing.reset(routing_token)
                    _current_channel.reset(token)
                yield item
        finally:
            await iterator.aclose()


def execution_context(
    context: object, defaults: RunnableConfig | None, config: RunnableConfig | None
) -> tuple[ChannelBinding | None, object, Mapping[str, object] | None]:
    """Split managed routing out of a managed run's context; direct runs keep theirs whole."""
    configurable = ensure_config(defaults, config)["configurable"]
    channel_run = is_channel_execution(configurable)
    scheduled_run = is_scheduled_execution(configurable)
    if not channel_run and not scheduled_run:
        return None, context, None
    raw = context if isinstance(context, Mapping) else {}
    # Older crons stored routing in configurable, before context was supported.
    # Read their channel only here, under the authenticated schedule marker.
    legacy = configurable if scheduled_run and configurable.get("mda_schedule") else {}
    # Agent Server can filter context against the authored schema. Its mirrored
    # routing remains in the graph defaults; explicit context takes precedence.
    routing = {
        key: value
        for key, value in configurable.items()
        if is_managed_key(key) and (scheduled_run or key == "mda_channel")
    }
    if routing.get("mda_channel") is None and legacy.get("channel") is not None:
        routing["mda_channel"] = legacy["channel"]
    routing.update({key: value for key, value in raw.items() if is_managed_key(key)})
    rest = {key: value for key, value in raw.items() if not is_managed_key(key)}
    # Native schema filtering can leave only the old delivery field from a
    # mirrored cron context. Remove that transport object, not authored values.
    if isinstance(legacy.get("channel"), Mapping) and rest.get("channel") == legacy["channel"]:
        rest.pop("channel", None)
    binding = (
        resolve_channel_binding({"mda_channel": routing.get("mda_channel")}, configurable)
        if channel_run
        else None
    )
    # A cron saved without a context fires with its configurable mirrored into
    # the context; the principal there is never authored context.
    if "langgraph_auth_user" in rest:
        rest = {}
    return binding, rest or None, routing


def is_channel_execution(configurable: Mapping[str, object] | None) -> bool:
    user = _field(configurable, "langgraph_auth_user")
    return all(
        isinstance(value, str) and value.strip()
        for value in (_field(user, "mda_source_provider"), _field(user, "mda_source_thread_id"))
    )


def is_scheduled_execution(configurable: Mapping[str, object] | None) -> bool:
    return _field(_field(configurable, "langgraph_auth_user"), "mda_scheduled_run") is True


def execution_config(
    defaults: RunnableConfig | None, config: RunnableConfig | None
) -> RunnableConfig | None:
    """Clear inherited application context without changing other native fields."""
    effective = ensure_config(defaults, config)
    if not is_channel_execution(effective["configurable"]):
        return config
    parent = effective["configurable"].get(CONFIG_KEY_RUNTIME)
    if not isinstance(parent, Runtime):
        return config
    return patch_configurable(config, {CONFIG_KEY_RUNTIME: parent.override(context=None)})
