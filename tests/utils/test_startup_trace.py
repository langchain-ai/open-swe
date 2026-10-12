from typing import Any, cast
from unittest.mock import MagicMock

import pytest
from langchain.agents.middleware import AgentState
from langchain_core.tracers.langchain import LangChainTracer
from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from typing_extensions import TypedDict

from openswe.middleware.prepare_run import BasePrepareRunMiddleware
from openswe.utils import startup_trace
from openswe.utils.startup_trace import aphase, flush_phases


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
    startup_trace._PHASES.clear()
    yield
    startup_trace._PHASES.clear()


async def test_failed_phase_is_replayed_with_its_error() -> None:
    with pytest.raises(RuntimeError):
        async with aphase("thread-2", "sandbox.boot"):
            raise RuntimeError("boom")

    client = await _flush_in_traced_node("thread-2")

    boot = next(run for run in client.updated if run["name"] == "sandbox.boot")
    assert boot["error"] == "RuntimeError: boom"


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
