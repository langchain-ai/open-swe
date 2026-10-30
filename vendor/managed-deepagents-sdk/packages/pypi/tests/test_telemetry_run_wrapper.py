"""Emission when a run ends, on both the async and sync graph entry points."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, TypedDict
from uuid import uuid4

import pytest
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.callbacks.manager import CallbackManager
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from managed_deepagents import DeepAgentDefinition, runtime
from managed_deepagents._telemetry import (
    PosthogTransport,
    RunConfiguration,
    RunIdentity,
    TelemetryClient,
)
from managed_deepagents._telemetry import run as telemetry_run
from managed_deepagents._telemetry.run import MemoryObservations, PendingRun, start_run
from managed_deepagents._telemetry.run_wrapper import (
    flush_run_telemetry_for_tests,
    observe_run,
    set_run_telemetry_client_for_tests,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

IDENTITY = RunIdentity(
    tenant_id="tenant-123",
    workspace_id="tenant-123",
    org_id=None,
    principal_id=None,
    langsmith_host_url=None,
    deployment_id="deployment-1",
    deployment_name="support-bot",
    environment="deployed",
    sdk_language="python",
    sdk_version="0.7.2",
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


class _Recorder:
    def __init__(self, *, fails: bool = False) -> None:
        self.events: list[dict[str, Any]] = []
        self._fails = fails

    def send(self, event: dict[str, Any]) -> None:
        if self._fails:
            raise RuntimeError("posthog down")
        self.events.append(event)


@pytest.fixture
def recorder() -> Iterator[_Recorder]:
    sink = _Recorder()
    set_run_telemetry_client_for_tests(TelemetryClient(sink))
    yield sink
    set_run_telemetry_client_for_tests(None)


def _pending() -> PendingRun:
    return start_run(IDENTITY, CONFIGURATION, started_monotonic_ms=1000)


class _State(TypedDict):
    n: int


def _counter(*, pauses: bool = False) -> Any:
    def bump(state: _State) -> _State:
        if pauses:
            interrupt("approve")
        return {"n": state.get("n", 0) + 1}

    graph = StateGraph(_State)
    graph.add_node("bump", bump)
    graph.add_edge(START, "bump")
    graph.add_edge("bump", END)
    return graph.compile(checkpointer=MemorySaver() if pauses else None)


def _boom() -> Any:
    def explode(_s: _State) -> _State:
        raise RuntimeError("node exploded")

    graph = StateGraph(_State)
    graph.add_node("boom", explode)
    graph.add_edge(START, "boom")
    graph.add_edge("boom", END)
    return graph.compile()


def _invoke(graph: Any, *, asynchronous: bool) -> object:
    return asyncio.run(graph.ainvoke({"n": 0})) if asynchronous else graph.invoke({"n": 0})


@pytest.mark.parametrize("asynchronous", [False, True])
def test_reports_once_for_invoke(recorder: _Recorder, asynchronous: bool) -> None:
    compiled = observe_run(_counter(), _pending())
    assert _invoke(compiled, asynchronous=asynchronous) == {"n": 1}
    assert flush_run_telemetry_for_tests()
    assert len(recorder.events) == 1
    assert recorder.events[0]["outcome"] == "success"
    assert recorder.events[0]["duration_ms"] >= 0


def test_reports_a_streamed_run(recorder: _Recorder) -> None:
    compiled = observe_run(_counter(), _pending())

    for _chunk in compiled.stream({"n": 0}):
        pass

    assert flush_run_telemetry_for_tests()
    assert [event["outcome"] for event in recorder.events] == ["success"]


@pytest.mark.parametrize("asynchronous", [False, True])
def test_reports_a_node_failure(recorder: _Recorder, asynchronous: bool) -> None:
    compiled = observe_run(_boom(), _pending())
    with pytest.raises(RuntimeError, match="node exploded"):
        _invoke(compiled, asynchronous=asynchronous)
    assert flush_run_telemetry_for_tests()
    assert [event["outcome"] for event in recorder.events] == ["failure"]


def test_reports_a_consumer_that_abandons_the_stream(recorder: _Recorder) -> None:
    compiled = observe_run(_counter(), _pending())

    stream = compiled.stream({"n": 0})
    next(iter(stream))
    stream.close()

    assert flush_run_telemetry_for_tests()
    assert [event["outcome"] for event in recorder.events] == ["failure"]


def test_reports_exactly_one_event_per_run(recorder: _Recorder) -> None:
    pending = _pending()
    compiled = observe_run(_counter(), pending)

    compiled.invoke({"n": 0})
    compiled.invoke({"n": 0})

    assert flush_run_telemetry_for_tests()
    assert len(recorder.events) == 1
    assert pending.emitted is True


def test_a_failing_transport_does_not_fail_the_run() -> None:
    set_run_telemetry_client_for_tests(TelemetryClient(_Recorder(fails=True)))
    try:
        compiled = observe_run(_counter(), _pending())

        assert compiled.invoke({"n": 0}) == {"n": 1}
    finally:
        set_run_telemetry_client_for_tests(None)


@pytest.mark.parametrize("asynchronous", [False, True])
def test_delivery_does_not_delay_the_run_or_depend_on_its_loop(asynchronous: bool) -> None:
    release = Event()
    delivered = Event()

    def slow_post(_url: str, _headers: object, _body: bytes, _timeout: float) -> None:
        release.wait()
        delivered.set()

    set_run_telemetry_client_for_tests(
        TelemetryClient(
            PosthogTransport(api_key="public-test-key", post=slow_post, max_wait_seconds=0)
        )
    )
    graph = observe_run(_counter(), _pending())

    def invoke() -> object:
        if asynchronous:
            return asyncio.run(graph.ainvoke({"n": 0}))
        return graph.invoke({"n": 0})

    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            result = executor.submit(invoke)
            try:
                assert result.result(timeout=2) == {"n": 1}
                assert not delivered.is_set()
            finally:
                release.set()
            assert delivered.wait(timeout=2)
    finally:
        set_run_telemetry_client_for_tests(None)


# Every other case injects a client, so this is the only one exercising the
# lazy build. conftest sets MDA_TELEMETRY_DISABLED, so no transport exists.
def test_runs_normally_when_telemetry_is_opted_out() -> None:
    compiled = observe_run(_counter(), _pending())

    assert compiled.invoke({"n": 0}) == {"n": 1}


def test_async_stream_close_reports_failure(recorder: _Recorder) -> None:
    async def run() -> None:
        stream = observe_run(_counter(), _pending()).astream({"n": 0})
        await anext(stream)
        await stream.aclose()
        assert flush_run_telemetry_for_tests()
        assert [event["outcome"] for event in recorder.events] == ["failure"]

    asyncio.run(run())


def test_reports_interrupt_stream_envelopes(recorder: _Recorder) -> None:
    compiled = observe_run(_counter(pauses=True), _pending())
    list(
        compiled.stream(
            {"n": 0},
            {"configurable": {"thread_id": "interrupt-test"}},
            stream_mode=["updates", "values"],
            subgraphs=True,
        )
    )
    assert flush_run_telemetry_for_tests()
    assert [event["outcome"] for event in recorder.events] == ["interrupted"]


def test_reports_interrupt_from_ainvoke(recorder: _Recorder) -> None:
    async def run() -> None:
        compiled = observe_run(_counter(pauses=True), _pending())
        result = await compiled.ainvoke({"n": 0}, {"configurable": {"thread_id": "interrupt-test"}})
        assert "__interrupt__" in result

    asyncio.run(run())
    assert flush_run_telemetry_for_tests()
    assert [event["outcome"] for event in recorder.events] == ["interrupted"]


def test_survives_server_copy_and_preserves_callbacks(recorder: _Recorder) -> None:
    finished: list[object] = []

    class ExistingListener(BaseCallbackHandler):
        def on_chain_end(self, outputs: object, **kwargs: Any) -> None:
            finished.append(outputs)

    pending = start_run(IDENTITY, CONFIGURATION, run_id="platform-run", thread_id="thread")
    graph = _counter().with_config({"callbacks": [ExistingListener()], "metadata": {"app": "test"}})
    compiled = observe_run(graph, pending)
    # Exactly the reconstruction used by Agent Server's get_graph().
    copied = compiled.copy(update={"checkpointer": MemorySaver(), "store": None})
    assert copied.config["metadata"]["app"] == "test"
    assert copied.invoke({"n": 0}, {"configurable": {"thread_id": "thread"}}) == {"n": 1}
    assert finished
    assert flush_run_telemetry_for_tests()
    assert len(recorder.events) == 1
    assert recorder.events[0]["run_id"] == "platform-run"
    assert recorder.events[0]["run_id_source"] == "platform"
    assert recorder.events[0]["thread_id"] == "thread"


def test_invocation_callbacks_and_tracing_parent_are_preserved(recorder: _Recorder) -> None:
    finished: list[object] = []

    class ExistingListener(BaseCallbackHandler):
        def on_chain_end(self, outputs: object, **kwargs: Any) -> None:
            finished.append(outputs)

    manager = CallbackManager([ExistingListener()], parent_run_id=uuid4())
    observe_run(_counter(), _pending()).invoke({"n": 0}, {"callbacks": manager})
    assert finished
    assert flush_run_telemetry_for_tests()
    assert [event["outcome"] for event in recorder.events] == ["success"]


@pytest.mark.parametrize("pause_at", ["before", "after"])
def test_static_interrupts(recorder: _Recorder, pause_at: str) -> None:
    graph = observe_run(_counter(), _pending())
    graph.invoke({"n": 0}, **{f"interrupt_{pause_at}": ["bump"]})
    assert flush_run_telemetry_for_tests()
    assert [event["outcome"] for event in recorder.events] == ["interrupted"]


def test_resume_is_a_separate_run(recorder: _Recorder) -> None:
    graph = _counter(pauses=True)
    config = {"configurable": {"thread_id": "resume-test"}}
    observe_run(graph, _pending()).invoke({"n": 0}, config)
    assert observe_run(graph, _pending()).invoke(Command(resume=True), config) == {"n": 1}
    assert flush_run_telemetry_for_tests()
    assert [event["outcome"] for event in recorder.events] == ["interrupted", "success"]
    assert recorder.events[0]["run_id"] != recorder.events[1]["run_id"]


def test_concurrent_runs_do_not_share_events(recorder: _Recorder) -> None:
    async def run() -> None:
        await asyncio.gather(
            *[
                observe_run(
                    _counter(), start_run(IDENTITY, CONFIGURATION, run_id=f"run-{i}")
                ).ainvoke({"n": i})
                for i in range(3)
            ]
        )

    asyncio.run(run())
    assert flush_run_telemetry_for_tests()
    assert {event["run_id"] for event in recorder.events} == {"run-0", "run-1", "run-2"}
    assert len(recorder.events) == 3
    assert all(event["outcome"] == "success" for event in recorder.events)


@pytest.mark.parametrize(("name", "used"), [("browser_snapshot", True), ("read_file", False)])
def test_reports_a_browser_tool_call_as_browser_use(
    recorder: _Recorder, name: str, used: bool
) -> None:
    from langchain_core.runnables import RunnableConfig  # noqa: PLC0415
    from langchain_core.tools import StructuredTool  # noqa: PLC0415

    called = StructuredTool.from_function(lambda: "ok", name=name, description="test tool")

    def call(state: _State, config: RunnableConfig) -> _State:
        called.invoke({}, config)
        return {"n": state["n"] + 1}

    graph = StateGraph(_State)
    graph.add_node("call", call)
    graph.add_edge(START, "call")
    graph.add_edge("call", END)
    observe_run(graph.compile(), _pending()).invoke({"n": 0})

    assert flush_run_telemetry_for_tests()
    assert [event["browser_used"] for event in recorder.events] == [used]


def test_cancellation_cleans_up_and_reports_cancelled(recorder: _Recorder) -> None:
    async def run() -> None:
        started = asyncio.Event()
        closed = asyncio.Event()

        async def wait(_state: _State) -> _State:
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                closed.set()
            return {"n": 1}

        graph = StateGraph(_State)
        graph.add_node("wait", wait)
        graph.add_edge(START, "wait")
        graph.add_edge("wait", END)
        task = asyncio.create_task(observe_run(graph.compile(), _pending()).ainvoke({"n": 0}))
        await asyncio.wait_for(started.wait(), timeout=2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert closed.is_set()
        assert flush_run_telemetry_for_tests()
        assert [event["outcome"] for event in recorder.events] == ["cancelled"]

    asyncio.run(run())


def test_schema_inspection_emits_nothing(recorder: _Recorder) -> None:
    graph = observe_run(_counter(), _pending())
    graph.get_input_jsonschema()
    graph.get_output_jsonschema()
    assert flush_run_telemetry_for_tests()
    assert recorder.events == []


def test_invalid_configuration_before_callbacks_emits_nothing(recorder: _Recorder) -> None:
    with pytest.raises(ValueError, match="recursion_limit"):
        observe_run(_counter(), _pending()).invoke({"n": 0}, {"recursion_limit": 0})
    assert flush_run_telemetry_for_tests()
    assert recorder.events == []


def test_compile_returns_callback_configured_graph(
    recorder: _Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    graph = _counter()
    clock = SimpleNamespace(monotonic=lambda: 1.0, time_ns=runtime.time.time_ns)
    monkeypatch.setattr(runtime, "time", clock)
    monkeypatch.setattr(telemetry_run, "time", clock)
    monkeypatch.setattr(runtime, "create_deep_agent", lambda **_kwargs: graph)
    monkeypatch.setattr(runtime, "_append_channel_response_format_middleware", lambda _merged: None)
    definition = DeepAgentDefinition({"model": "test-model", "metadata": {"app": "test"}})
    compiled = runtime.compile_managed_agent(
        definition,
        {
            "metadata": {"run_id": "platform-run"},
            "configurable": {
                "langgraph_auth_user": {
                    "identity": "caller",
                    "mda_user_id": "caller",
                    "mda_agent_auth_principal_id": "run-principal",
                    "permissions": [],
                }
            },
        },
    )
    copied = compiled.copy(update={"checkpointer": None, "store": None})
    assert copied.config["metadata"]["app"] == "test"
    clock.monotonic = lambda: 2.234
    assert copied.invoke({"n": 0}) == {"n": 1}
    assert flush_run_telemetry_for_tests()
    assert len(recorder.events) == 1
    assert recorder.events[0]["run_id"] == "platform-run"
    assert recorder.events[0]["principal_id"] == "run-principal"
    assert recorder.events[0]["duration_ms"] == 1234
