"""Config defaults for native LangGraph execution."""

import asyncio
from collections.abc import AsyncIterator, Iterator, Mapping
from contextlib import AbstractContextManager, AsyncExitStack, nullcontext
from contextvars import Token
from functools import cached_property
from typing import Any, cast
from uuid import UUID

from langchain_core.callbacks import AsyncCallbackManager
from langchain_core.runnables.config import RunnableConfig
from langchain_core.tracers.langchain import LangChainTracer
from langchain_core.tracers.schemas import Run
from langgraph._internal._config import ensure_config, patch_configurable
from langgraph._internal._constants import CONFIG_KEY_CHECKPOINTER, CONFIG_KEY_RUNTIME
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph.state import CompiledStateGraph
from langgraph.runtime import Runtime
from langgraph.types import StateSnapshot
from langsmith import get_tracing_context, tracing_context

from managed_deepagents._channels.binding import (
    ChannelBinding,
    _current_channel,
    _current_routing,
    resolve_channel_binding,
)
from managed_deepagents._channels.delivery import (
    INPUT_SOURCE,
    deliver_run_result,
    delivers_result,
)
from managed_deepagents._channels.run_output import RunOutput, _current_output
from managed_deepagents._identity_runtime import _field
from managed_deepagents._managed_thread import RunThread, _current_thread, build_run_thread
from managed_deepagents._memory.async_util import await_if_needed
from managed_deepagents._telemetry.otel import (
    LogContext,
    current_log_context,
    enter_log_context,
    exit_log_context,
    trace_id_from_metadata,
)
from managed_deepagents.run_context import is_managed_key
from managed_deepagents.run_resources import _current_resources

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


class ManagedTurnTracer(LangChainTracer):
    """Close a managed invocation's trace tree when its root finishes."""

    @cached_property
    def roots(self) -> list[Run]:
        return []

    @property
    def root(self) -> Run | None:
        return self.roots[0] if self.roots else None

    def copy_with_metadata_defaults(
        self, *, metadata: Mapping[str, str] | None = None, tags: list[str] | None = None
    ) -> LangChainTracer:
        tracer = super().copy_with_metadata_defaults(metadata=metadata, tags=tags)
        if isinstance(tracer, ManagedTurnTracer):
            tracer.roots = self.roots
        return tracer

    def _start_trace(self, run: Run) -> None:
        if self.root is None:
            self.roots.append(run)
            run.metadata.pop("end_reason", None)
        elif self.root.end_time is not None:
            run.extra["__disabled"] = True
        super()._start_trace(run)

    def _errored_chain_run(
        self, error: BaseException, inputs: dict[str, object] | None, run_id: UUID
    ) -> Run:
        run = super()._errored_chain_run(error, inputs, run_id)
        if run is self.root:
            reason = "failed"
            if isinstance(error, (asyncio.CancelledError, GeneratorExit)):
                reason = "cancelled" if isinstance(error, GeneratorExit) else "failed"
                pending: list[BaseException] = [error]
                seen: set[int] = set()
                while pending:
                    cause = pending.pop()
                    if id(cause) in seen:
                        continue
                    seen.add(id(cause))
                    if isinstance(cause, TimeoutError) or type(cause).__name__ == "UserTimeout":
                        reason = "failed"
                        break
                    if type(cause).__name__ == "UserInterrupt":
                        reason = (
                            "superseded"
                            if str(cause).startswith("superseded by run ")
                            and "(multitask_strategy=interrupt)" in str(cause)
                            else "cancelled"
                        )
                    pending.extend(arg for arg in cause.args if isinstance(arg, BaseException))
                    if cause.__cause__ is not None:
                        pending.append(cause.__cause__)
                if reason != "failed":
                    run.error = None
            run.metadata["end_reason"] = reason
        return run

    def _end_trace(self, run: Run) -> None:
        if run is self.root:
            run.metadata.setdefault("end_reason", "failed" if run.error else "completed")
            pending = list(run.child_runs)
            while pending:
                child = pending.pop()
                pending.extend(child.child_runs)
                if child.end_time is None:
                    child.end(end_time=run.end_time)
                    self._update_run_single(child)
                    child.extra["__disabled"] = True
        super()._end_trace(run)

    @classmethod
    def for_run(cls, config: RunnableConfig) -> RunnableConfig:
        """Preserve tracing destinations and parents while isolating invocation lifecycle."""
        manager = AsyncCallbackManager.configure(inheritable_callbacks=config.get("callbacks"))
        for handler in list(manager.handlers):
            if not isinstance(handler, LangChainTracer):
                continue
            tracer = cls(
                example_id=handler.example_id,
                project_name=handler.project_name,
                address=handler.address,
                client=handler.client,
                tags=handler.tags,
                metadata=handler.tracing_metadata,
                run_map=dict(handler.run_map),
                order_map=dict(handler.order_map),
                _external_run_ids=dict(handler._external_run_ids),  # noqa: SLF001
            )
            manager.remove_handler(handler)
            manager.add_handler(tracer)
        return {**config, "callbacks": manager}


class ManagedCompiledAgent(CompiledStateGraph[Any, Any, Any, Any]):
    """Remove ingress transport before native context conversion, on every execution.

    Each step sees the run's channel and thread; when the stream ends, the run's
    result goes to its thread origin, or a scheduled run's ``deliver_to``.
    """

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
        thread = run_thread(binding, self.config, config)
        merged = _merged(self.config, config)
        log = run_log_context(self.config, config)
        output = None if self._keeps_state(merged) else RunOutput()
        config = execution_config(self.config, config)
        iterator = super().stream(input, config, context=context, **kwargs)
        try:
            while True:
                tokens = _enter_step(binding, routing, thread, output)
                log_token = enter_log_context(log)
                try:
                    with self._tracing(config):
                        item = next(iterator)
                except StopIteration:
                    self._deliver(thread, merged, routing, output)
                    return
                finally:
                    exit_log_context(log_token)
                    _exit_step(tokens)
                yield item
        finally:
            log_token = enter_log_context(log)
            try:
                with self._tracing(config):
                    iterator.close()
            finally:
                exit_log_context(log_token)

    async def astream(
        self,
        input: Any,
        config: RunnableConfig | None = None,
        *,
        context: Any = None,
        **kwargs: Any,
    ) -> AsyncIterator[Any]:
        binding, context, routing = execution_context(context, self.config, config)
        thread = run_thread(binding, self.config, config)
        merged = _merged(self.config, config)
        log = run_log_context(self.config, config)
        output = None if self._keeps_state(merged) else RunOutput()
        config = execution_config(self.config, config)
        with self._tracing(config):
            config = ManagedTurnTracer.for_run(ensure_config(self.config, config))
        iterator = super().astream(input, config, context=context, **kwargs)
        resources = AsyncExitStack()
        try:
            while True:
                tokens = _enter_step(binding, routing, thread, output)
                log_token = enter_log_context(log)
                try:
                    resource_token = _current_resources.set(resources)
                    try:
                        with self._tracing(config):
                            item = await anext(iterator)
                    finally:
                        _current_resources.reset(resource_token)
                except StopAsyncIteration:
                    await self._adeliver(thread, merged, routing, output)
                    return
                finally:
                    exit_log_context(log_token)
                    _exit_step(tokens)
                yield item
        finally:
            log_token = enter_log_context(log)
            try:
                try:
                    with self._tracing(config):
                        await iterator.aclose()
                finally:
                    await resources.aclose()
            finally:
                exit_log_context(log_token)

    def _deliver(
        self,
        thread: RunThread,
        merged: RunnableConfig,
        routing: Mapping[str, object] | None,
        output: RunOutput | None,
    ) -> None:
        """Send the finished stream's result to the run's thread origin or ``deliver_to``."""
        if not delivers_result(thread, merged, routing):
            return
        if output is not None:
            await_if_needed(self._deliver_output(thread, merged, routing, output))
            return
        # The run may have started from a named checkpoint; read the thread's latest.
        latest = _history_config(merged)
        history = self.get_state_history(latest, filter=_INPUT, limit=1)
        boundary = next(iter(history), None)
        await_if_needed(
            deliver_run_result(
                thread,
                merged,
                self.get_state(latest),
                boundary,
                routing=routing,
                subagent=_lead_thread_id(self.config, merged) is not None,
            )
        )

    async def _adeliver(
        self,
        thread: RunThread,
        merged: RunnableConfig,
        routing: Mapping[str, object] | None,
        output: RunOutput | None,
    ) -> None:
        if not delivers_result(thread, merged, routing):
            return
        if output is not None:
            await self._deliver_output(thread, merged, routing, output)
            return
        latest = _history_config(merged)
        boundary = None
        async for snapshot in self.aget_state_history(latest, filter=_INPUT, limit=1):
            boundary = snapshot
        await deliver_run_result(
            thread,
            merged,
            await self.aget_state(latest),
            boundary,
            routing=routing,
            subagent=_lead_thread_id(self.config, merged) is not None,
        )

    async def _deliver_output(
        self,
        thread: RunThread,
        merged: RunnableConfig,
        routing: Mapping[str, object] | None,
        output: RunOutput,
    ) -> None:
        """Deliver a stateless scheduled run's answer to its ``deliver_to``.

        It has no thread to read back, so its answer is what its agent ended
        with. A stateless run with a thread origin posts nothing, as before.
        """
        if thread.origin is not None or output.messages is None:
            return
        final = StateSnapshot(
            values={"messages": output.messages},
            next=(),
            config=merged,
            metadata=None,
            created_at=None,
            parent_config=None,
            tasks=(),
            interrupts=(),
        )
        await deliver_run_result(
            thread,
            merged,
            final,
            None,
            routing=routing,
            subagent=_lead_thread_id(self.config, merged) is not None,
            stateless=True,
        )

    def _keeps_state(self, config: RunnableConfig) -> bool:
        """Whether the run's state can be read back; a stateless run's cannot."""
        checkpointer = config["configurable"].get(CONFIG_KEY_CHECKPOINTER, self.checkpointer)
        return isinstance(checkpointer, BaseCheckpointSaver)


#: Lists the checkpoint LangGraph writes before a run's input.
_INPUT = {"source": INPUT_SOURCE}

_StepTokens = tuple[
    Token[ChannelBinding | None],
    Token[Mapping[str, object] | None],
    Token[RunThread | None],
    Token[RunOutput | None],
]


def _enter_step(
    binding: ChannelBinding | None,
    routing: Mapping[str, object] | None,
    thread: RunThread,
    output: RunOutput | None,
) -> _StepTokens:
    return (
        _current_channel.set(binding),
        _current_routing.set(routing),
        _current_thread.set(thread),
        _current_output.set(output),
    )


def _exit_step(tokens: _StepTokens) -> None:
    channel, routing, thread, output = tokens
    _current_output.reset(output)
    _current_thread.reset(thread)
    _current_routing.reset(routing)
    _current_channel.reset(channel)


def _history_config(config: RunnableConfig) -> RunnableConfig:
    """The run's thread at its latest checkpoint, without the one the run started from."""
    configurable = {
        key: value
        for key, value in config["configurable"].items()
        if key not in ("checkpoint_id", "checkpoint_map")
    }
    return {**config, "configurable": configurable}


def run_thread(
    binding: ChannelBinding | None,
    defaults: Mapping[str, object] | None,
    config: Mapping[str, object] | None,
) -> RunThread:
    """The run's ``runtime.thread``; an async subagent's is its lead's."""
    merged = _merged(defaults, config)
    configurable = merged["configurable"]
    return build_run_thread(
        thread_id=configurable.get("thread_id"),
        live=binding,
        metadata=merged.get("metadata"),
        configurable=configurable,
        lead_thread_id=_lead_thread_id(defaults, config),
    )


class SingleProjectManagedAgent(ManagedCompiledAgent):
    """Keep requested-project runs from also being copied to the deployment project."""

    replicate_traces = False


def _lead_thread_id(
    defaults: Mapping[str, object] | None, config: Mapping[str, object] | None
) -> str | None:
    """The lead's thread, for a channel caller's run of an async subagent the lead started.

    deepagents sends it as the parent reference, or, for a run with the lead's
    context, in the run metadata the Agent Server copies from the task thread. The
    subagent's thread is its lead's, so its pause is shown in the lead's channel
    thread and the user can answer the subagent there.
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


def run_log_context(defaults: RunnableConfig | None, config: RunnableConfig | None) -> LogContext:
    """One log context per run, under the trace id ingress handed over.

    A nested managed graph reuses its parent's context.
    """
    parent = current_log_context()
    if parent is not None:
        return parent
    merged = ensure_config(defaults, config)
    metadata: Mapping[str, object] = merged.get("metadata") or {}
    log = LogContext(trace_id=trace_id_from_metadata(metadata))
    for key, value in (
        ("run_id", metadata.get("run_id")),
        ("thread_id", merged["configurable"].get("thread_id")),
    ):
        if isinstance(value, str) and value:
            log.attributes[key] = value
    return log


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
