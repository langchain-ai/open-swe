"""Close a managed invocation's LangSmith trace when its root run ends.

Agent Server can close a run's stream, or cancel its task, while model and tool
work is still unwinding. Native tracing then leaves those spans open, records
spans that start after the run ended, and marks every cancellation as an error.
"""

import asyncio
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID

from langchain_core.callbacks import (
    BaseCallbackHandler,
    BaseCallbackManager,
    CallbackManager,
    Callbacks,
)
from langchain_core.runnables.config import RunnableConfig, var_child_runnable_config
from langchain_core.tracers.langchain import LangChainTracer
from langchain_core.tracers.schemas import Run
from langgraph._internal._config import ensure_config

#: Why a managed invocation's root run ended, as its trace metadata records it.
EndReason = Literal["completed", "failed", "cancelled", "superseded"]

#: Agent Server's ``langgraph_api.errors`` types, matched by name: the runtime
#: does not depend on Agent Server. ``UserRollback`` subclasses ``UserInterrupt``.
_USER_INTERRUPT = "UserInterrupt"
_USER_TIMEOUT = "UserTimeout"
#: The reason Agent Server gives a run that a newer run on its thread replaced.
_SUPERSEDED = re.compile(r"superseded by run \S+ \(multitask_strategy=(interrupt|rollback)\)")


@dataclass
class _Invocation:
    """The lifecycle every copy of one invocation's tracer shares."""

    root: Run | None = None
    end_reason: EndReason | None = None


class ManagedTurnTracer(LangChainTracer):
    """A ``LangChainTracer`` whose first run is the root of one managed invocation.

    The root records a bounded ``end_reason``. When it ends, its unfinished
    children end with it, and nothing that starts or ends afterwards is sent.
    """

    _invocation: _Invocation

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._invocation = _Invocation()

    def copy_with_metadata_defaults(
        self, *, metadata: Mapping[str, str] | None = None, tags: list[str] | None = None
    ) -> LangChainTracer:
        """Copies LangChain makes for nested metadata still belong to this invocation."""
        copied = super().copy_with_metadata_defaults(metadata=metadata, tags=tags)
        if isinstance(copied, ManagedTurnTracer):
            copied._invocation = self._invocation  # noqa: SLF001 - same class
        return copied

    def _start_trace(self, run: Run) -> None:
        invocation = self._invocation
        if invocation.root is None:
            invocation.root = run
        elif invocation.root.end_time is not None:
            # Work still unwinding after its run ended is not part of the run.
            run.extra["__disabled"] = True
        super()._start_trace(run)

    def _errored_chain_run(
        self, error: BaseException, inputs: dict[str, Any] | None, run_id: UUID
    ) -> Run:
        run = super()._errored_chain_run(error, inputs, run_id)
        if run is self._invocation.root:
            reason = end_reason(error)
            self._invocation.end_reason = reason
            if reason != "failed":
                run.error = None
        return run

    def _end_trace(self, run: Run) -> None:
        invocation = self._invocation
        if run is invocation.root:
            reason = invocation.end_reason or ("failed" if run.error else "completed")
            # A fresh dict: the run's metadata can be its callback manager's.
            run.extra["metadata"] = {**run.metadata, "end_reason": reason}
            pending = list(run.child_runs)
            while pending:
                child = pending.pop()
                pending.extend(child.child_runs)
                if child.end_time is None:
                    child.end(end_time=run.end_time)
                    self._update_run_single(child)
                    child.extra["__disabled"] = True
        super()._end_trace(run)


def end_reason(error: BaseException) -> EndReason:
    """Classify the error a managed root run ended with.

    Only a recognised user interruption or a closed stream is no failure. A bare
    ``CancelledError`` (such as Agent Server's ``asyncio.wait_for`` run deadline)
    is unexplained, and a cancellation caused by a timeout stays a failure.
    """
    if not isinstance(error, asyncio.CancelledError | GeneratorExit):
        return "failed"
    reason: EndReason = "cancelled" if isinstance(error, GeneratorExit) else "failed"
    pending: list[BaseException] = [error]
    seen: set[int] = set()
    while pending:
        cause = pending.pop()
        if id(cause) in seen:
            continue
        seen.add(id(cause))
        names = {cls.__name__ for cls in type(cause).__mro__}
        if isinstance(cause, TimeoutError) or _USER_TIMEOUT in names:
            return "failed"
        if _USER_INTERRUPT in names:
            reason = "superseded" if _SUPERSEDED.fullmatch(str(cause)) else "cancelled"
        pending.extend(arg for arg in cause.args if isinstance(arg, BaseException))
        if cause.__cause__ is not None:
            pending.append(cause.__cause__)
    return reason


def turn_trace_config(
    defaults: RunnableConfig | None, config: RunnableConfig | None
) -> RunnableConfig | None:
    """Trace a top-level managed invocation with its own ``ManagedTurnTracer``.

    Each LangSmith tracer the invocation would use is replaced by one with the
    same destination, tags, metadata and known parents (including a parent from
    the LangSmith tracing context), so its trace lands where it would have. A
    managed graph nested in a traced run keeps that run's tracers and lifecycle:
    LangGraph merges the run's tracers back in, so replacing them would trace twice.
    Agent Server's own ambient config carries no callbacks.
    """
    ambient = var_child_runnable_config.get()
    if ambient is not None and _has_tracer(ambient.get("callbacks")):
        return config
    # Resolves environment tracing and the tracing context's parent run.
    manager = CallbackManager.configure(
        inheritable_callbacks=ensure_config(defaults, config).get("callbacks")
    )
    # Keyed by run map: with metadata in the tracing context, langchain-core gives the
    # manager one copy of a tracer for the run and another for its children. Copies share
    # a run map, and must share a turn tracer, or the children trace apart from the run.
    replaced: dict[int, ManagedTurnTracer] = {}
    for handler in [*manager.handlers, *manager.inheritable_handlers]:
        if isinstance(handler, LangChainTracer) and id(handler.run_map) not in replaced:
            replaced[id(handler.run_map)] = _turn_tracer(handler)
    if not replaced:
        return config

    def turn_tracer(handler: BaseCallbackHandler) -> BaseCallbackHandler:
        if isinstance(handler, LangChainTracer):
            return replaced[id(handler.run_map)]
        return handler

    manager.handlers = [turn_tracer(handler) for handler in manager.handlers]
    manager.inheritable_handlers = [
        turn_tracer(handler) for handler in manager.inheritable_handlers
    ]
    return {**(config or {}), "callbacks": manager}


def without_tracers(defaults: RunnableConfig | None) -> RunnableConfig | None:
    """``defaults`` without their LangSmith tracers, or ``None`` when they bind none.

    LangGraph merges a graph's bound config (``with_config``) into each call's
    callbacks as it streams. Once ``turn_trace_config`` has replaced a bound tracer,
    the graph must run under these defaults, or the original traces the run beside
    its replacement. Every other bound callback is kept.
    """
    callbacks = (defaults or {}).get("callbacks")
    kept: Callbacks
    if isinstance(callbacks, BaseCallbackManager):
        handlers = [*callbacks.handlers, *callbacks.inheritable_handlers]
        if not any(isinstance(handler, LangChainTracer) for handler in handlers):
            return None
        manager = callbacks.copy()
        manager.handlers = [h for h in manager.handlers if not isinstance(h, LangChainTracer)]
        manager.inheritable_handlers = [
            h for h in manager.inheritable_handlers if not isinstance(h, LangChainTracer)
        ]
        kept = manager
    elif _has_tracer(callbacks):
        kept = [h for h in callbacks or () if not isinstance(h, LangChainTracer)]
    else:
        return None
    return {**(defaults or {}), "callbacks": kept}


def _has_tracer(callbacks: Callbacks) -> bool:
    handlers = callbacks.handlers if isinstance(callbacks, BaseCallbackManager) else callbacks
    return any(isinstance(handler, LangChainTracer) for handler in handlers or ())


def _turn_tracer(handler: LangChainTracer) -> ManagedTurnTracer:
    """``handler``'s destination and known runs, under a lifecycle of its own."""
    return ManagedTurnTracer(
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
