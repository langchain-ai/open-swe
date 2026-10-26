from collections import OrderedDict
from collections.abc import Iterator
from unittest.mock import AsyncMock, MagicMock

import agent
import httpx2
import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, ToolCall
from langgraph.graph.state import CompiledStateGraph
from langgraph.store.memory import InMemoryStore
from langgraph_sdk.runtime import _ExecutionRuntime, _ReadRuntime
from managed_deepagents import runtime as mda_runtime
from managed_deepagents.runtime import ManagedRunConfig, compile_managed_agent_async
from pydantic import JsonValue
from runtime_api.catalog import Catalog, LoopRules, OperationSpec, SubagentSpec

_THREAD: ManagedRunConfig = {"configurable": {"thread_id": "thread-1"}}
_WORKERS = {"general-purpose": "open-swe--general-purpose"}


def _context(token: str, grant: str) -> dict[str, object]:
    """A run of the thread as dispatch starts it: the same catalog, granted to this run."""
    catalog = Catalog(
        operations=[
            OperationSpec(name="web_search", description="Search.", parameters={}, grant=grant)
        ],
        subagents=[SubagentSpec(name="researcher", description="Research.", system_prompt="")],
        skill_sources=["/skills/org/"],
        loop=LoopRules(),
    )
    return {
        "graph": "reviewer",
        "__run_token": token,
        "invocation_id": f"invocation-{token}",
        "__catalog": catalog.model_dump(mode="json"),
    }


def _replies() -> Iterator[AIMessage]:
    for _ in range(2):
        yield AIMessage(content="", tool_calls=[ToolCall(name="web_search", args={}, id="call-1")])
        yield AIMessage(content="done")


async def test_a_threads_runs_and_reads_share_the_graph_its_runs_build(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reads see the thread through the graph its runs write it with, and a later run
    reuses that graph while calling the backend as itself."""
    monkeypatch.setenv("OPEN_SWE_BACKEND_URL", "https://backend.invalid")
    monkeypatch.setattr(mda_runtime._ThreadGraph, "_built", OrderedDict())
    monkeypatch.setattr(GenericFakeChatModel, "bind_tools", lambda self, *args, **kwargs: self)
    monkeypatch.setattr(agent, "_DEFAULT_MODEL", GenericFakeChatModel(messages=_replies()))
    request = httpx2.Request("POST", "https://backend.invalid")
    operations: list[tuple[str, JsonValue]] = []

    async def post(
        url: str, *, headers: dict[str, str], json: dict[str, JsonValue], **kwargs: object
    ) -> httpx2.Response:
        hook = url.rsplit("/", 1)[-1]
        if hook == "operation":
            operations.append((headers["Authorization"], json["grant"]))
            return httpx2.Response(200, request=request, json={"content": "found"})
        return httpx2.Response(200, request=request, json={"files": []} if hook == "skills" else {})

    client = MagicMock(is_closed=False)
    client.post = AsyncMock(side_effect=post)
    monkeypatch.setattr("rt.backend.httpx2.AsyncClient", lambda **kwargs: client)
    store = InMemoryStore()

    async def run(token: str, grant: str) -> CompiledStateGraph:
        context = _context(token, grant)
        graph = await compile_managed_agent_async(
            agent.agent,
            _THREAD,
            runtime=_ExecutionRuntime(
                access_context="threads.create_run", store=store, context=context
            ),
            report_telemetry=False,
            async_subagent_graphs=_WORKERS,
        )
        # The run context as the Agent Server hands it over, which LangGraph reads into the schema.
        await graph.ainvoke(
            {"messages": [("user", "Review it")]},
            _THREAD,
            context=context,  # ty: ignore[invalid-argument-type]
        )
        return graph

    first = await run("token-1", "grant-1")
    second = await run("token-2", "grant-2")

    assert second.nodes["model"] is first.nodes["model"]
    assert operations == [("Bearer token-1", "grant-1"), ("Bearer token-2", "grant-2")]

    # Another replica, which never built the thread's graph, reads its state.
    monkeypatch.setattr(mda_runtime._ThreadGraph, "_built", OrderedDict())
    read = await compile_managed_agent_async(
        agent.agent,
        _THREAD,
        runtime=_ReadRuntime(access_context="threads.read", store=store),
        report_telemetry=False,
        async_subagent_graphs=_WORKERS,
    )

    assert set(read.channels) == set(first.channels)
    # Code mode's and the run's skills' state, which a read built from no run lacks.
    assert {"_quickjs_snapshot_payload", "skills_metadata"} <= set(read.channels)
