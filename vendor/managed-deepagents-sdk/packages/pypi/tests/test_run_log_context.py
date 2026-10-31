"""Run-side log records share the trace id ingress handed over."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, TypedDict

import pytest
from langgraph.graph import END, START, StateGraph

from managed_deepagents._run_input import ManagedCompiledAgent
from managed_deepagents._telemetry import RunConfiguration, RunIdentity, TelemetryClient
from managed_deepagents._telemetry.otel import record_log
from managed_deepagents._telemetry.run import MemoryObservations, start_run
from managed_deepagents._telemetry.run_wrapper import (
    flush_run_telemetry_for_tests,
    observe_run,
    set_run_telemetry_client_for_tests,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

    from tests.conftest import CapturedLogs

TRACE_ID = "4bf92f3577b34da6a3ce929d0e0e4736"

IDENTITY = RunIdentity(
    tenant_id=None,
    workspace_id=None,
    org_id=None,
    principal_id=None,
    langsmith_host_url=None,
    deployment_id=None,
    deployment_name=None,
    environment="deployed",
    sdk_language="python",
    sdk_version="0.0.0",
)
CONFIGURATION = RunConfiguration(
    channel="http",
    sandbox_declared=False,
    identity_declared=False,
    skills_declared=False,
    backend_kind="state",
    context_source="none",
    memory=MemoryObservations(layers_declared=[], layers_mounted=[], user_denied_reason=None),
    provider_event_type=None,
    user_resolution=None,
    subagent_role="lead",
    browser_declared=False,
)


class _State(TypedDict):
    n: int


class _Sink:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def send(self, event: dict[str, Any]) -> None:
        self.events.append(event)


@pytest.fixture
def sent() -> Iterator[_Sink]:
    sink = _Sink()
    set_run_telemetry_client_for_tests(TelemetryClient(sink))
    yield sink
    set_run_telemetry_client_for_tests(None)


@pytest.fixture
def logs(captured_logs: CapturedLogs, sent: _Sink) -> CapturedLogs:
    """Captured records, with the run client installed through ``sent``."""
    del sent
    return captured_logs


def _graph(*, fails: bool) -> Any:
    def step(state: _State) -> _State:
        if fails:
            raise RuntimeError("node exploded")
        return {"n": state.get("n", 0) + 1}

    graph = StateGraph(_State)
    graph.add_node("step", step)
    graph.add_edge(START, "step")
    graph.add_edge("step", END)
    compiled = graph.compile()
    compiled.__class__ = ManagedCompiledAgent
    return observe_run(
        compiled,
        start_run(
            IDENTITY,
            CONFIGURATION,
            run_id="platform-run",
            thread_id="thread-1",
            started_monotonic_ms=0,
        ),
    )


CONFIG = {
    "metadata": {"run_id": "platform-run", "mda_trace_id": TRACE_ID},
    "configurable": {"thread_id": "thread-1"},
}


@pytest.mark.parametrize("asynchronous", [False, True])
def test_logs_the_run_under_the_handed_over_trace_id(
    logs: CapturedLogs, asynchronous: bool
) -> None:
    graph = _graph(fails=False)
    if asynchronous:
        asyncio.run(graph.ainvoke({"n": 0}, CONFIG))
    else:
        graph.invoke({"n": 0}, CONFIG)

    assert logs.records
    assert all(record.trace_id == TRACE_ID for record in logs.records)
    completed = next(record for record in logs.records if record.body == "run completed")
    assert completed.attributes["outcome"] == "success"
    assert completed.attributes["run_id"] == "platform-run"
    assert completed.attributes["thread_id"] == "thread-1"


def test_records_a_root_failure_with_its_error(logs: CapturedLogs, sent: _Sink) -> None:
    with pytest.raises(RuntimeError, match="node exploded"):
        _graph(fails=True).invoke({"n": 0}, CONFIG)

    records = [(record.severity, record.body) for record in logs.records]
    assert records == [("ERROR", "run failed"), ("INFO", "run completed")]
    assert logs.records[0].attributes["exception.type"] == "RuntimeError"
    assert logs.records[1].attributes["outcome"] == "failure"
    # The completion record carries the id PostHog dedupes the event on.
    assert flush_run_telemetry_for_tests()
    assert len(sent.events) == 1
    assert logs.records[1].attributes["event_id"] == sent.events[0]["event_id"]


class _ClosingFails:
    """A native iterator that logs one step, then fails to close."""

    def __iter__(self) -> _ClosingFails:
        return self

    def __next__(self) -> object:
        record_log("INFO", "step")
        raise StopIteration

    def close(self) -> None:
        raise RuntimeError("close failed")

    def __aiter__(self) -> _ClosingFails:
        return self

    async def __anext__(self) -> object:
        record_log("INFO", "step")
        raise StopAsyncIteration

    async def aclose(self) -> None:
        raise RuntimeError("close failed")


@pytest.mark.parametrize("asynchronous", [False, True])
def test_queues_the_runs_records_when_closing_the_iterator_fails(
    logs: CapturedLogs, monkeypatch: pytest.MonkeyPatch, asynchronous: bool
) -> None:
    graph = _graph(fails=False)
    native = ManagedCompiledAgent.__mro__[1]
    monkeypatch.setattr(native, "stream", lambda *_args, **_kwargs: _ClosingFails())
    monkeypatch.setattr(native, "astream", lambda *_args, **_kwargs: _ClosingFails())

    async def consume() -> None:
        async for _ in graph.astream({"n": 0}, CONFIG):
            pass

    def run() -> None:
        if asynchronous:
            asyncio.run(consume())
        else:
            list(graph.stream({"n": 0}, CONFIG))

    with pytest.raises(RuntimeError, match="close failed"):
        run()

    assert [(record.body, record.trace_id) for record in logs.records] == [("step", TRACE_ID)]
