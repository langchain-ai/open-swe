from collections import OrderedDict
from collections.abc import Iterator
from unittest.mock import AsyncMock, MagicMock

import agent
import httpx2
import pytest
from deepagents.backends import StateBackend
from deepagents.backends.protocol import ExecuteResponse
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, ToolCall
from langgraph.graph.state import CompiledStateGraph
from langgraph.store.memory import InMemoryStore
from langgraph_sdk.runtime import _ExecutionRuntime, _ReadRuntime
from managed_deepagents import runtime as mda_runtime
from managed_deepagents.runtime import ManagedRunConfig, compile_managed_agent_async
from managed_deepagents.sandbox import SandboxDefinition
from pydantic import JsonValue
from runtime_api.catalog import (
    Catalog,
    LoopRules,
    OperationSpec,
    SandboxCreate,
    SandboxSpec,
    SubagentSpec,
)

_WORKERS = {"general-purpose": "open-swe--general-purpose"}

type BackendCalls = list[tuple[str, str, str | None]]


def _thread(thread_id: str) -> ManagedRunConfig:
    return {"configurable": {"thread_id": thread_id}}


def _context(graph: str, token: str, catalog: Catalog) -> dict[str, object]:
    """A run as dispatch starts it."""
    return {
        "graph": graph,
        "__run_token": token,
        "invocation_id": f"invocation-{token}",
        "__catalog": catalog.model_dump(mode="json"),
    }


def _catalog(operation: str, grant: str, *, snapshot: str | None = None) -> Catalog:
    return Catalog(
        operations=[
            OperationSpec(name=operation, description="An operation.", parameters={}, grant=grant)
        ],
        subagents=[SubagentSpec(name="reviewer", description="Reviews.", system_prompt="")],
        skill_sources=["/skills/org/"],
        loop=LoopRules(),
        sandbox=None
        if snapshot is None
        else SandboxSpec(
            create=SandboxCreate(
                snapshot_id=snapshot,
                fs_capacity_bytes=1,
                idle_ttl_seconds=60,
                delete_after_stop_seconds=60,
            )
        ),
    )


def _calling(*calls: ToolCall) -> GenericFakeChatModel:
    """A model that makes each call in turn, each followed by an answer."""

    def replies() -> Iterator[AIMessage]:
        for call in calls:
            yield AIMessage(content="", tool_calls=[call])
            yield AIMessage(content="done")

    return GenericFakeChatModel(messages=replies())


@pytest.fixture
def backend_calls(monkeypatch: pytest.MonkeyPatch) -> BackendCalls:
    """Every call the runs make on the backend: its endpoint, its bearer and, for an
    operation, the name and grant it was called with."""
    monkeypatch.setenv("OPEN_SWE_BACKEND_URL", "https://backend.invalid")
    monkeypatch.setattr(mda_runtime._SharedGraphs, "_built", OrderedDict())
    monkeypatch.setattr(GenericFakeChatModel, "bind_tools", lambda self, *args, **kwargs: self)
    request = httpx2.Request("POST", "https://backend.invalid")
    calls: BackendCalls = []

    async def post(
        url: str, *, headers: dict[str, str], json: dict[str, JsonValue], **kwargs: object
    ) -> httpx2.Response:
        hook = url.rsplit("/", 1)[-1]
        operation = f"{json['name']}:{json['grant']}" if hook == "operation" else None
        calls.append((hook, headers["Authorization"], operation))
        if hook == "operation":
            return httpx2.Response(200, request=request, json={"content": "found"})
        return httpx2.Response(200, request=request, json={"files": []} if hook == "skills" else {})

    client = MagicMock(is_closed=False)
    client.post = AsyncMock(side_effect=post)
    monkeypatch.setattr("rt.backend.httpx2.AsyncClient", lambda **kwargs: client)
    return calls


async def _run(
    store: InMemoryStore, thread_id: str, context: dict[str, object]
) -> CompiledStateGraph:
    graph = await compile_managed_agent_async(
        agent.agent,
        _thread(thread_id),
        runtime=_ExecutionRuntime(
            access_context="threads.create_run", store=store, context=context
        ),
        report_telemetry=False,
        async_subagent_graphs=_WORKERS,
    )
    await graph.ainvoke(
        {"messages": [("user", "Review it")]},
        _thread(thread_id),
        # The run context as the Agent Server hands it over, which LangGraph reads into the schema.
        context=context,  # ty: ignore[invalid-argument-type]
    )
    return graph


async def test_every_run_and_read_shares_one_graph_and_calls_the_backend_as_itself(
    monkeypatch: pytest.MonkeyPatch, backend_calls: BackendCalls
) -> None:
    """Two threads with catalogs of their own run on one compiled graph, each offered and
    calling its own operations with its own token and grants; a replica that never ran
    either reads them through a graph with the same channels."""
    monkeypatch.setattr(
        agent,
        "_DEFAULT_MODEL",
        _calling(
            ToolCall(name="web_search", args={}, id="call-1"),
            ToolCall(name="list_findings", args={}, id="call-2"),
        ),
    )
    store = InMemoryStore()

    first = await _run(
        store, "thread-1", _context("reviewer", "token-1", _catalog("web_search", "grant-1"))
    )
    second = await _run(
        store, "thread-2", _context("reviewer", "token-2", _catalog("list_findings", "grant-2"))
    )

    assert second.nodes["model"] is first.nodes["model"]
    assert [(bearer, operation) for _, bearer, operation in backend_calls if operation] == [
        ("Bearer token-1", "web_search:grant-1"),
        ("Bearer token-2", "list_findings:grant-2"),
    ]
    assert {bearer for _, bearer, _ in backend_calls} == {"Bearer token-1", "Bearer token-2"}

    # Another replica, which never built either graph, reads a thread's state.
    monkeypatch.setattr(mda_runtime._SharedGraphs, "_built", OrderedDict())
    read = await compile_managed_agent_async(
        agent.agent,
        _thread("thread-1"),
        runtime=_ReadRuntime(access_context="threads.read", store=store),
        report_telemetry=False,
        async_subagent_graphs=_WORKERS,
    )

    assert set(read.channels) == set(first.channels)
    # Code mode's and the run's skills' state, which every graph has.
    assert {"_quickjs_snapshot_payload", "skills_metadata"} <= set(read.channels)


async def test_each_run_on_the_shared_graph_works_in_its_own_threads_box(
    monkeypatch: pytest.MonkeyPatch, backend_calls: BackendCalls
) -> None:
    """The graph is compiled once, yet each run's shell reaches its own thread's box,
    with the options its own catalog gives."""
    opened: set[tuple[object, object]] = set()

    def resolve(
        sandbox: SandboxDefinition, config: ManagedRunConfig | None, *args: object
    ) -> StateBackend:
        configurable = (config or {}).get("configurable") or {}
        thread_id = configurable.get("thread_id")
        opened.add((thread_id, sandbox.options.get("snapshot_id")))
        box = MagicMock(spec=StateBackend)
        box.id = f"box-{thread_id}"
        box.aexecute = AsyncMock(return_value=ExecuteResponse(output="ok", exit_code=0))
        return box

    monkeypatch.setattr(mda_runtime, "_resolve_managed_sandbox", resolve)
    monkeypatch.setattr(
        agent,
        "_DEFAULT_MODEL",
        _calling(
            ToolCall(name="execute", args={"command": "ls"}, id="call-1"),
            ToolCall(name="execute", args={"command": "ls"}, id="call-2"),
        ),
    )
    store = InMemoryStore()

    first = await _run(
        store, "thread-1", _context("agent", "token-1", _catalog("web_search", "g1", snapshot="a"))
    )
    second = await _run(
        store, "thread-2", _context("agent", "token-2", _catalog("web_search", "g2", snapshot="b"))
    )

    assert second.nodes["model"] is first.nodes["model"]
    assert opened == {("thread-1", "a"), ("thread-2", "b")}
