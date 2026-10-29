"""Report run completion through LangGraph lifecycle callbacks."""

from __future__ import annotations

import os
from threading import Lock
from typing import TYPE_CHECKING, Any, TypeVar, cast

from managed_deepagents._telemetry.client import TelemetryClient
from managed_deepagents._telemetry.gates import is_telemetry_disabled
from managed_deepagents._telemetry.otel import LogAttributes, record_log
from managed_deepagents._telemetry.run import finish_run
from managed_deepagents._telemetry.transport import posthog_transport

if TYPE_CHECKING:
    from uuid import UUID

    from langgraph.callbacks import GraphInterruptEvent

    from managed_deepagents._telemetry.run import PendingRun, RunCompletedEvent, RunOutcome
    from managed_deepagents._telemetry.transport.posthog import PosthogTransport

_client: TelemetryClient[RunCompletedEvent] | None = None
_transport: PosthogTransport | None = None
_client_resolved = False
_client_lock = Lock()

AgentT = TypeVar("AgentT")


def _run_client() -> TelemetryClient[RunCompletedEvent] | None:
    global _client, _transport, _client_resolved  # noqa: PLW0603 - process-wide cache, resolved once
    with _client_lock:
        if not _client_resolved:
            _transport = None if is_telemetry_disabled(os.environ) else posthog_transport()
            if _transport is not None:
                _client = TelemetryClient(_transport)
            _client_resolved = True
        return _client


def set_run_telemetry_client_for_tests(override: TelemetryClient[RunCompletedEvent] | None) -> None:
    """Test-only transport override."""
    global _client, _transport, _client_resolved  # noqa: PLW0603 - mirrors the process-wide cache
    with _client_lock:
        _client = override
        _transport = None
        _client_resolved = override is not None


def flush_run_telemetry_for_tests() -> bool:
    """Wait for queued events from the built-in transport."""
    return _transport is None or _transport.idle()


def observe_run(agent: AgentT, pending: PendingRun) -> AgentT:  # noqa: C901 - lazy callback class
    """Return a graph whose lifecycle listener survives Agent Server's graph copy.

    Each managed run builds its own graph and pending event. LangGraph merges
    these callbacks with existing graph and invocation callbacks.
    """
    configure = getattr(agent, "with_config", None)
    if not callable(configure):
        return agent

    # Authoring-only installs do not need LangGraph.
    from langgraph.callbacks import GraphCallbackHandler  # noqa: PLC0415

    class RunListener(GraphCallbackHandler):
        run_inline = True

        def __init__(self) -> None:
            self.root: UUID | None = None
            self.interrupted = False

        def on_chain_start(
            self,
            serialized: object,  # noqa: ARG002 - callback signature
            inputs: object,  # noqa: ARG002 - callback signature
            *,
            run_id: UUID,
            **_kwargs: Any,
        ) -> None:
            # The bound graph starts before its children. It can itself have a
            # tracing parent, so parent_run_id=None is not a reliable filter.
            if self.root is None:
                self.root = run_id

        def on_interrupt(self, event: GraphInterruptEvent) -> None:
            if event.run_id == self.root:
                self.interrupted = True

        def on_chain_end(
            self,
            outputs: object,  # noqa: ARG002 - callback signature
            *,
            run_id: UUID,
            **_kwargs: Any,
        ) -> None:
            if run_id == self.root:
                self.report("interrupted" if self.interrupted else "success")

        def on_chain_error(
            self,
            error: BaseException,
            *,
            run_id: UUID,
            **_kwargs: Any,
        ) -> None:
            if run_id == self.root:
                record_log("ERROR", "run failed", error=error)
                self.report("failure")

        def report(self, outcome: RunOutcome) -> None:
            if pending.emitted:
                return
            pending.emitted = True
            event = finish_run(pending, outcome)
            record_log("INFO", "run completed", attributes=_completion_attributes(event))
            client = _run_client()
            if client is not None:
                client.record(event)

    return cast("AgentT", configure({"callbacks": [RunListener()]}))


#: Event bookkeeping that says nothing about the run. ``event_id`` stays, so the
#: record joins to its PostHog event.
_EVENT_ONLY_FIELDS = frozenset({"event_type", "schema_version", "created_at", "distinct_id"})


def _completion_attributes(event: RunCompletedEvent) -> LogAttributes:
    """The ``run_completed`` fields as attributes of the completion log record."""
    return {
        key: value
        for key, value in event.items()
        if key not in _EVENT_ONLY_FIELDS and isinstance(value, str | int | float | bool)
    }
