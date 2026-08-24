import asyncio
from typing import Any, cast
from unittest.mock import MagicMock

import pytest
from langchain.agents.middleware import AgentState
from langchain_core.tracers.langchain import LangChainTracer
from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from typing_extensions import TypedDict

from agent.middleware.prepare_run import BasePrepareRunMiddleware
from agent.utils import startup_trace
from agent.utils.startup_trace import aphase, flush_phases


class _FakeClient:
    def __init__(self) -> None:
        self.created: list[dict[str, Any]] = []
        self.updated: list[dict[str, Any]] = []

    def create_run(self, **kwargs: Any) -> None:
        self.created.append(kwargs)

    def update_run(self, run_id: Any, **kwargs: Any) -> None:
        del run_id
        self.updated.append(kwargs)


class _State(TypedDict):
    done: bool


async def _flush_in_traced_node(thread_id: str) -> _FakeClient:
    async def node(state: Any) -> Any:
        del state
        flush_phases(thread_id)
        return {"done": True}

    graph = StateGraph(_State)
    graph.add_node("node", node)
    graph.add_edge(START, "node")
    graph.add_edge("node", END)
    client = _FakeClient()
    tracer = LangChainTracer(client=cast(Any, client), project_name="test")
    await graph.compile().ainvoke({"done": False}, config=cast(Any, {"callbacks": [tracer]}))
    return client


@pytest.fixture(autouse=True)
def _clean_phases() -> Any:
    yield
    startup_trace._PHASES.clear()


async def test_phases_replay_as_child_spans_of_the_current_run() -> None:
    async with aphase("thread-1", "sandbox.boot", snapshot_id="snap-1"):
        await asyncio.sleep(0.01)
    async with aphase("thread-1", "sandbox.git_identity"):
        pass

    client = await _flush_in_traced_node("thread-1")

    names = [run["name"] for run in client.created]
    assert names == ["LangGraph", "node", "startup", "sandbox.boot", "sandbox.git_identity"]
    boot = next(run for run in client.created if run["name"] == "sandbox.boot")
    assert boot["inputs"]["snapshot_id"] == "snap-1"
    wrapper = next(run for run in client.updated if run["name"] == "startup")
    assert [phase["name"] for phase in wrapper["outputs"]["phases"]] == [
        "sandbox.boot",
        "sandbox.git_identity",
    ]
    assert wrapper["outputs"]["phases"][0]["elapsed_ms"] >= 10


async def test_failed_phase_is_replayed_with_its_error() -> None:
    with pytest.raises(RuntimeError):
        async with aphase("thread-2", "sandbox.boot"):
            raise RuntimeError("boom")

    client = await _flush_in_traced_node("thread-2")

    boot = next(run for run in client.updated if run["name"] == "sandbox.boot")
    assert boot["error"] == "RuntimeError: boom"


async def test_flush_without_a_run_tree_drops_the_phases() -> None:
    async with aphase("thread-3", "sandbox.boot"):
        pass

    flush_phases("thread-3")

    assert "thread-3" not in startup_trace._PHASES


async def test_unfinished_phase_is_kept_for_the_next_flush() -> None:
    phase = startup_trace._open("thread-5", "sandbox.boot", {})
    assert phase is not None

    client = await _flush_in_traced_node("thread-5")

    assert [run["name"] for run in client.created] == ["LangGraph", "node"]
    startup_trace._close(phase, None)
    client = await _flush_in_traced_node("thread-5")
    assert [run["name"] for run in client.created] == [
        "LangGraph",
        "node",
        "startup",
        "sandbox.boot",
    ]


async def test_prepare_middleware_flushes_phases_even_when_prepare_fails() -> None:
    class _Failing(BasePrepareRunMiddleware):
        _thread_id = "thread-4"

        async def _prepare(self, state: Any, runtime: Any) -> dict[str, Any]:
            del state, runtime
            raise RuntimeError("no sandbox")

    async with aphase("thread-4", "sandbox.boot"):
        pass

    with pytest.raises(RuntimeError):
        await _Failing().abefore_agent(
            cast(AgentState, {"messages": []}), cast(Runtime[None], MagicMock())
        )

    assert "thread-4" not in startup_trace._PHASES


async def test_prepare_middleware_flushes_phases_when_the_latch_skips_prepare() -> None:
    class _Latched(BasePrepareRunMiddleware):
        _thread_id = "thread-6"

        async def _prepare(self, state: Any, runtime: Any) -> dict[str, Any]:
            del state, runtime
            raise AssertionError("prepare should be latched out")

    middleware = _Latched()
    fingerprint = middleware._prepare_fingerprint(
        cast(Any, {"messages": []}), cast(Runtime[None], MagicMock())
    )
    async with aphase("thread-6", "factory.thread_settings"):
        pass

    assert (
        await middleware.abefore_agent(
            cast(
                AgentState, {"messages": [], "run_prepared": True, "run_prepared_for": fingerprint}
            ),
            cast(Runtime[None], MagicMock()),
        )
        is None
    )

    assert "thread-6" not in startup_trace._PHASES
