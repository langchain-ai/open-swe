"""Config defaults for native LangGraph execution."""

from collections.abc import AsyncIterator, Iterator, Mapping, Sequence
from contextlib import AbstractContextManager, nullcontext
from typing import Any, cast

from langchain_core.runnables.config import RunnableConfig
from langgraph._internal._config import ensure_config, patch_configurable
from langgraph._internal._constants import CONFIG_KEY_RUNTIME
from langgraph.graph.state import CompiledStateGraph
from langgraph.runtime import Runtime
from langgraph.types import Interrupt
from langsmith import get_tracing_context, tracing_context

from managed_deepagents._channels.binding import (
    ChannelBinding,
    _current_channel,
    _current_routing,
    _lead_channel,
    resolve_channel_binding,
)
from managed_deepagents._channels.schedule_delivery import (
    SAVED_CHANNEL_KEY,
    _delivering_notification,
    is_task_notification_input,
    report_notification_interrupts,
    restored_channel,
)
from managed_deepagents._identity_runtime import _field
from managed_deepagents._memory.async_util import await_if_needed
from managed_deepagents.run_context import is_managed_key

#: Run ``configurable`` key deepagents uses to name the agent that started an async subagent.
_PARENT_KEY = "deepagents_parent"
#: Run metadata key (copied from the task thread) naming an async subagent's lead thread.
_PARENT_THREAD_METADATA_KEY = "parent_thread_id"


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

    replicate_traces: bool = True

    def _tracing(self, config: RunnableConfig | None) -> AbstractContextManager[None]:
        project = ensure_config(self.config, config)["configurable"].get("__langsmith_project__")
        if self.replicate_traces or not project:
            return nullcontext()
        context = get_tracing_context()
        replicas = [
            replica
            for replica in context["replicas"] or ()
            if replica.get("project_name") == project
        ]
        if not replicas:
            return nullcontext()
        context["replicas"] = replicas
        parent = context["parent"]
        if parent is not None:
            context["parent"] = parent.model_copy(update={"replicas": replicas})
        return tracing_context(**context)

    def stream(
        self,
        input: Any,
        config: RunnableConfig | None = None,
        *,
        context: Any = None,
        **kwargs: Any,
    ) -> Iterator[Any]:
        binding, context, routing = execution_context(context, self.config, config)
        notifying = False
        reports_to = None
        if binding is None and _may_restore_channel(input, self.config, config):
            binding = reports_to = self._saved_channel(_merged(self.config, config), config)
            notifying = binding is not None
        elif binding is None and (lead := _lead_thread_id(self.config, config)):
            reports_to = self._saved_channel(_thread_config(lead), config)
        config = execution_config(self.config, config)
        iterator = super().stream(input, config, context=context, **kwargs)
        try:
            while True:
                token = _current_channel.set(binding)
                lead_token = _lead_channel.set(None if binding is not None else reports_to)
                notify_token = _delivering_notification.set(notifying)
                routing_token = _current_routing.set(routing)
                try:
                    with self._tracing(config):
                        item = next(iterator)
                except StopIteration:
                    if reports_to is not None:
                        merged = _merged(self.config, config)
                        _report_pause(reports_to, merged, self.get_state(merged).interrupts)
                    return
                finally:
                    _delivering_notification.reset(notify_token)
                    _current_routing.reset(routing_token)
                    _lead_channel.reset(lead_token)
                    _current_channel.reset(token)
                yield item
        finally:
            with self._tracing(config):
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
        notifying = False
        reports_to = None
        if binding is None and _may_restore_channel(input, self.config, config):
            binding = reports_to = await self._asaved_channel(_merged(self.config, config), config)
            notifying = binding is not None
        elif binding is None and (lead := _lead_thread_id(self.config, config)):
            reports_to = await self._asaved_channel(_thread_config(lead), config)
        config = execution_config(self.config, config)
        iterator = super().astream(input, config, context=context, **kwargs)
        try:
            while True:
                token = _current_channel.set(binding)
                lead_token = _lead_channel.set(None if binding is not None else reports_to)
                notify_token = _delivering_notification.set(notifying)
                routing_token = _current_routing.set(routing)
                try:
                    with self._tracing(config):
                        item = await anext(iterator)
                except StopAsyncIteration:
                    if reports_to is not None:
                        merged = _merged(self.config, config)
                        snapshot = await self.aget_state(merged)
                        await _areport_pause(reports_to, merged, snapshot.interrupts)
                    return
                finally:
                    _delivering_notification.reset(notify_token)
                    _current_routing.reset(routing_token)
                    _lead_channel.reset(lead_token)
                    _current_channel.reset(token)
                yield item
        finally:
            with self._tracing(config):
                await iterator.aclose()

    def _saved_channel(
        self, thread_config: RunnableConfig, config: RunnableConfig | None
    ) -> ChannelBinding | None:
        """The channel saved on a thread, if this run's caller may deliver to it."""
        saved = self.get_state(thread_config).values.get(SAVED_CHANNEL_KEY)
        return restored_channel(saved, _configurable(self.config, config))

    async def _asaved_channel(
        self, thread_config: RunnableConfig, config: RunnableConfig | None
    ) -> ChannelBinding | None:
        saved = (await self.aget_state(thread_config)).values.get(SAVED_CHANNEL_KEY)
        return restored_channel(saved, _configurable(self.config, config))


class SingleProjectManagedAgent(ManagedCompiledAgent):
    """Keep requested-project runs from also being copied to the deployment project."""

    replicate_traces = False


def _lead_thread_id(
    defaults: Mapping[str, object] | None, config: Mapping[str, object] | None
) -> str | None:
    """The lead's thread, for a channel caller's run of an async subagent the lead started.

    deepagents sends it as the parent reference, or, for a run with the lead's
    context, in the run metadata the Agent Server copies from the task thread. The
    subagent's pause is shown in the lead's channel thread, so the user can answer
    the subagent there.
    """
    merged = _merged(defaults, config)
    configurable = merged["configurable"]
    if not is_channel_execution(configurable):
        return None
    thread_id = _field(configurable.get(_PARENT_KEY), "thread_id") or _field(
        merged.get("metadata"), _PARENT_THREAD_METADATA_KEY
    )
    if (
        not isinstance(thread_id, str)
        or not thread_id
        or thread_id == configurable.get("thread_id")
    ):
        return None
    return thread_id


def _thread_config(thread_id: str) -> RunnableConfig:
    return {"configurable": {"thread_id": thread_id}}


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


def _merged(
    defaults: Mapping[str, object] | None, config: Mapping[str, object] | None
) -> RunnableConfig:
    return ensure_config(
        cast("RunnableConfig | None", defaults), cast("RunnableConfig | None", config)
    )


def _configurable(
    defaults: Mapping[str, object] | None, config: Mapping[str, object] | None
) -> Mapping[str, object]:
    return _merged(defaults, config)["configurable"]


async def _areport_pause(
    binding: ChannelBinding, config: RunnableConfig, pending: Sequence[Interrupt]
) -> None:
    """Show a notification run's pending interrupts in the channel thread it restored."""
    interrupts: list[dict[str, object]] = [
        {"id": item.id, "value": item.value} for item in pending if item.id
    ]
    thread_id = config["configurable"].get("thread_id")
    run_id = _run_id(config)
    if interrupts and thread_id and run_id:
        await report_notification_interrupts(
            binding, run_thread_id=str(thread_id), run_id=run_id, interrupts=interrupts
        )


def _report_pause(
    binding: ChannelBinding, config: RunnableConfig, pending: Sequence[Interrupt]
) -> None:
    await_if_needed(_areport_pause(binding, config, pending))


def _run_id(config: RunnableConfig) -> str | None:
    """The Agent Server run executing this graph, wherever the config carries it."""
    metadata = config.get("metadata") or {}
    for value in (
        config.get("run_id"),
        config["configurable"].get("run_id"),
        metadata.get("run_id"),
    ):
        if value:
            return str(value)
    return None


def _may_restore_channel(
    input_value: object, defaults: Mapping[str, object] | None, config: Mapping[str, object] | None
) -> bool:
    """A channel caller's task notification run, which has no channel delivery of its own."""
    return is_task_notification_input(input_value) and is_channel_execution(
        _configurable(defaults, config)
    )


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
