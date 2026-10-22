"""Report run completion through LangGraph lifecycle callbacks."""

from __future__ import annotations

import atexit
import os
from threading import Lock
from typing import TYPE_CHECKING, Any, TypeVar, cast

from managed_deepagents._telemetry.background import BackgroundTelemetryClient
from managed_deepagents._telemetry.client import TelemetryClient
from managed_deepagents._telemetry.gates import is_telemetry_disabled
from managed_deepagents._telemetry.run import finish_run
from managed_deepagents._telemetry.transport import posthog_transport

if TYPE_CHECKING:
    from uuid import UUID

    from langgraph.callbacks import GraphInterruptEvent

    from managed_deepagents._telemetry.run import PendingRun, RunCompletedEvent, RunOutcome

_client: BackgroundTelemetryClient[RunCompletedEvent] | None = None
_client_resolved = False
_client_lock = Lock()

AgentT = TypeVar("AgentT")


def _run_client() -> BackgroundTelemetryClient[RunCompletedEvent] | None:
    global _client, _client_resolved  # noqa: PLW0603 - process-wide cache, resolved once
    with _client_lock:
        if not _client_resolved:
            transport = None if is_telemetry_disabled(os.environ) else posthog_transport()
            if transport is not None:
                _client = BackgroundTelemetryClient(TelemetryClient(transport))
            _client_resolved = True
        return _client


def set_run_telemetry_client_for_tests(override: TelemetryClient[RunCompletedEvent] | None) -> None:
    """Test-only transport override."""
    global _client, _client_resolved  # noqa: PLW0603 - mirrors the process-wide cache
    with _client_lock:
        previous = _client
        _client = BackgroundTelemetryClient(override) if override is not None else None
        _client_resolved = override is not None
    if previous is not None:
        previous.close()


def flush_run_telemetry_for_tests() -> bool:
    """Wait for queued test events without creating a transport."""
    return _client is None or _client.flush()


@atexit.register
def _shutdown_run_telemetry() -> None:
    if _client is not None:
        _client.close()


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
            error: BaseException,  # noqa: ARG002 - callback signature
            *,
            run_id: UUID,
            **_kwargs: Any,
        ) -> None:
            if run_id == self.root:
                self.report("failure")

        def report(self, outcome: RunOutcome) -> None:
            if pending.emitted:
                return
            pending.emitted = True
            client = _run_client()
            if client is not None:
                client.record(finish_run(pending, outcome))

    return cast("AgentT", configure({"callbacks": [RunListener()]}))
