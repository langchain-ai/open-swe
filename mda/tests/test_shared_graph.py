import asyncio
import gc
import weakref
from collections import OrderedDict
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import agent
import httpx2
import pytest
from deepagents.backends import FilesystemBackend, StateBackend
from deepagents.backends.protocol import ExecuteResponse, WriteResult
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, BaseMessage, SystemMessage, ToolCall, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import BaseTool
from langgraph.graph.state import CompiledStateGraph
from langgraph.store.memory import InMemoryStore
from langgraph_sdk.runtime import _ExecutionRuntime, _ReadRuntime
from managed_deepagents import runtime as mda_runtime
from managed_deepagents.runtime import ManagedRunConfig, compile_managed_agent_async
from managed_deepagents.sandbox import SandboxDefinition
from pydantic import JsonValue
from rt.backend import Run, current_run
from rt.run_tools import RunCodeInterpreter
from runtime_api.catalog import (
    Catalog,
    LoopRules,
    ModelSpec,
    OperationSpec,
    RunModels,
    SandboxCreate,
    SandboxSpec,
    SubagentSpec,
)

_WORKERS = {"general-purpose": "open-swe--general-purpose"}

type BackendCalls = list[tuple[str, str, str | None]]
type Prepared = list[JsonValue]


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
def prepared() -> Prepared:
    """The box each run reported as it prepared."""
    return []


@pytest.fixture
def operation_results() -> dict[str, str]:
    return {}


@pytest.fixture
def backend_calls(
    monkeypatch: pytest.MonkeyPatch, prepared: Prepared, operation_results: dict[str, str]
) -> BackendCalls:
    """Record backend calls with each run's bearer, operation name and grant."""
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
        if hook == "prepare":
            prepared.append(json.get("sandbox_id"))
        if hook == "operation":
            return httpx2.Response(
                200,
                request=request,
                json={"content": operation_results.get(str(operation), "found")},
            )
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
    monkeypatch: pytest.MonkeyPatch, backend_calls: BackendCalls, prepared: Prepared
) -> None:
    """The graph is compiled once, yet each run opens its own thread's box before it
    prepares, and its shell reaches that box, with the options its own catalog gives."""
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
    assert prepared == ["box-thread-1", "box-thread-2"]
    assert opened == {("thread-1", "a"), ("thread-2", "b")}


async def test_large_tool_results_remain_readable_with_or_without_a_box(
    monkeypatch: pytest.MonkeyPatch,
    backend_calls: BackendCalls,
    operation_results: dict[str, str],
    tmp_path: Path,
) -> None:
    content = "\n".join(f"result line {i}: " + "x" * 100 for i in range(2000))
    operation_results["lookup:grant"] = content

    def resolve(
        sandbox: SandboxDefinition, config: ManagedRunConfig | None, *args: object
    ) -> StateBackend:
        snapshot = str(sandbox.options["snapshot_id"])
        root = tmp_path / snapshot
        root.mkdir(exist_ok=True)
        disk = FilesystemBackend(root_dir=root, virtual_mode=True)
        box = MagicMock(spec=StateBackend)
        box.id = snapshot
        box.awrite = disk.awrite
        box.aread = disk.aread
        return box

    monkeypatch.setattr(mda_runtime, "_resolve_managed_sandbox", resolve)
    results: dict[str, ToolMessage] = {}

    async def generate(
        model: GenericFakeChatModel, messages: list[BaseMessage], **kwargs: object
    ) -> ChatResult:
        results.update(
            (message.tool_call_id, message)
            for message in messages
            if isinstance(message, ToolMessage)
        )
        if "lookup" not in results:
            reply = AIMessage(
                content="", tool_calls=[ToolCall(name="lookup", args={}, id="lookup")]
            )
        elif "read" not in results:
            assert "/large_tool_results/lookup" in results["lookup"].text
            assert len(results["lookup"].text) < len(content)
            reply = AIMessage(
                content="",
                tool_calls=[
                    ToolCall(
                        name="read_file",
                        args={
                            "file_path": "/large_tool_results/lookup",
                            "offset": 1000,
                            "limit": 1,
                        },
                        id="read",
                    )
                ],
            )
        else:
            assert results["read"].status == "success"
            assert content.splitlines()[1000] in results["read"].text
            reply = AIMessage(content="done")
        return ChatResult(generations=[ChatGeneration(message=reply)])

    monkeypatch.setattr(GenericFakeChatModel, "_agenerate", generate)
    monkeypatch.setattr(agent, "_DEFAULT_MODEL", _calling())
    store = InMemoryStore()
    graphs: list[CompiledStateGraph] = []
    for snapshot in (None, "selected-box-a", "selected-box-b", None):
        results.clear()
        graphs.append(
            await _run(
                store,
                f"large-result-{len(graphs)}",
                _context("reviewer", "token", _catalog("lookup", "grant", snapshot=snapshot)),
            )
        )
        assert "read" in results
        if snapshot is not None:
            saved = tmp_path / snapshot / "large_tool_results" / "lookup"
            assert saved.read_text() == content
    assert all(graph.nodes["model"] is graphs[0].nodes["model"] for graph in graphs)
    assert not (tmp_path / "large_tool_results" / "lookup").exists()
    assert not any(hook.startswith("files.") for hook, _, _ in backend_calls)


@pytest.mark.parametrize("failure", [OSError("storage unavailable"), WriteResult(error="full")])
async def test_boxless_eviction_failure_returns_truncated_content(
    monkeypatch: pytest.MonkeyPatch,
    backend_calls: BackendCalls,
    operation_results: dict[str, str],
    failure: OSError | WriteResult,
) -> None:
    content = "large result " * 20000
    operation_results["lookup:grant"] = content
    write = AsyncMock(
        side_effect=failure if isinstance(failure, OSError) else None,
        return_value=failure if isinstance(failure, WriteResult) else None,
    )
    monkeypatch.setattr(StateBackend, "awrite", write)
    results: list[ToolMessage] = []

    async def generate(
        model: GenericFakeChatModel, messages: list[BaseMessage], **kwargs: object
    ) -> ChatResult:
        results.extend(message for message in messages if isinstance(message, ToolMessage))
        reply = (
            AIMessage(content="done")
            if results
            else AIMessage(content="", tool_calls=[ToolCall(name="lookup", args={}, id="lookup")])
        )
        return ChatResult(generations=[ChatGeneration(message=reply)])

    monkeypatch.setattr(GenericFakeChatModel, "_agenerate", generate)
    monkeypatch.setattr(agent, "_DEFAULT_MODEL", _calling())
    await _run(
        InMemoryStore(),
        "failed-eviction",
        _context("reviewer", "token", _catalog("lookup", "grant")),
    )

    assert len(results) == 1
    assert results[0].status == "success"
    assert results[0].text.startswith(content[:1000])
    assert len(results[0].text) < len(content)
    assert "truncated: storage unavailable" in results[0].text
    assert "This run has no sandbox" not in results[0].text


@pytest.mark.parametrize("code_mode", [False, True])
async def test_finished_runs_release_their_tools_and_interpreters(
    monkeypatch: pytest.MonkeyPatch, backend_calls: BackendCalls, code_mode: bool
) -> None:
    runs: list[weakref.ReferenceType[Run]] = []

    def bind(
        model: GenericFakeChatModel, tools: list[BaseTool], **kwargs: object
    ) -> GenericFakeChatModel:
        runs.append(weakref.ref(current_run()))
        return model

    monkeypatch.setattr(GenericFakeChatModel, "bind_tools", bind)
    call = ToolCall(
        name="eval" if code_mode else "lookup",
        args={"code": "await tools.lookup({})"}
        if code_mode
        else {"display_description": "Look up"},
        id="lookup",
    )
    monkeypatch.setattr(agent, "_DEFAULT_MODEL", _calling(*[call for _ in range(5)]))
    catalog = _catalog("lookup", "grant").model_copy(
        update={
            "described": {"lookup": "What this does"},
            "code_only": ["lookup"] if code_mode else [],
        }
    )
    store = InMemoryStore()
    for index in range(5):
        await _run(store, f"gc-{index}", _context("agent", f"token-{index}", catalog))
    await asyncio.sleep(0)
    gc.collect()

    assert runs and all(run() is None for run in runs)
    assert len([operation for _, _, operation in backend_calls if operation]) == 5
    interpreter = next(
        middleware
        for middleware in agent._parts(agent.GraphInputs()).middleware
        if isinstance(middleware, RunCodeInterpreter)
    )
    assert not interpreter._registry._slots
    assert not interpreter._ptc_tools_by_slot


async def test_concurrent_runs_offer_and_call_only_their_own_operations(
    monkeypatch: pytest.MonkeyPatch, backend_calls: BackendCalls
) -> None:
    offered: dict[str, set[str]] = {}
    together = asyncio.Event()

    def bind(
        model: GenericFakeChatModel, tools: list[BaseTool], **kwargs: object
    ) -> GenericFakeChatModel:
        run = current_run()
        offered[run.bearer or ""] = {tool.name for tool in tools}
        return model

    async def generate(
        model: GenericFakeChatModel, messages: list[BaseMessage], **kwargs: object
    ) -> ChatResult:
        run = current_run()
        if not any(isinstance(message, ToolMessage) for message in messages):
            if len(offered) == 2:
                together.set()
            await asyncio.wait_for(together.wait(), timeout=10)
            reply = AIMessage(
                content="",
                tool_calls=[
                    ToolCall(name=run.catalog.operations[0].name, args={}, id="own-operation")
                ],
            )
        else:
            reply = AIMessage(content="done")
        return ChatResult(generations=[ChatGeneration(message=reply)])

    monkeypatch.setattr(GenericFakeChatModel, "bind_tools", bind)
    monkeypatch.setattr(GenericFakeChatModel, "_agenerate", generate)
    monkeypatch.setattr(agent, "_DEFAULT_MODEL", _calling())
    store = InMemoryStore()
    await asyncio.gather(
        *(
            _run(
                store,
                f"concurrent-{i}",
                _context("agent", f"token-{i}", _catalog(f"operation_{i}", f"grant-{i}")),
            )
            for i in range(2)
        )
    )

    assert "operation_0" in offered["token-0"] - offered["token-1"]
    assert "operation_1" in offered["token-1"] - offered["token-0"]
    assert sorted((bearer, operation) for _, bearer, operation in backend_calls if operation) == [
        ("Bearer token-0", "operation_0:grant-0"),
        ("Bearer token-1", "operation_1:grant-1"),
    ]


async def test_failed_code_run_releases_its_interpreter(
    monkeypatch: pytest.MonkeyPatch, backend_calls: BackendCalls
) -> None:
    def replies() -> Iterator[AIMessage]:
        yield AIMessage(
            content="",
            tool_calls=[ToolCall(name="eval", args={"code": "await tools.lookup({})"}, id="eval")],
        )
        raise RuntimeError("model failed after eval")

    monkeypatch.setattr(agent, "_DEFAULT_MODEL", GenericFakeChatModel(messages=replies()))
    catalog = _catalog("lookup", "grant").model_copy(update={"code_only": ["lookup"]})
    with pytest.raises(RuntimeError, match="model failed after eval"):
        await _run(InMemoryStore(), "failed", _context("agent", "token", catalog))
    await asyncio.sleep(0)
    gc.collect()

    interpreter = next(
        middleware
        for middleware in agent._parts(agent.GraphInputs()).middleware
        if isinstance(middleware, RunCodeInterpreter)
    )
    assert any(operation == "lookup:grant" for _, _, operation in backend_calls)
    assert not interpreter._registry._slots
    assert not interpreter._ptc_tools_by_slot


async def test_code_prompt_uses_each_runs_schema_even_when_names_match(
    monkeypatch: pytest.MonkeyPatch, backend_calls: BackendCalls
) -> None:
    prompts: list[str] = []

    async def generate(
        model: GenericFakeChatModel, messages: list[BaseMessage], **kwargs: object
    ) -> ChatResult:
        prompts.append(
            "\n".join(message.text for message in messages if isinstance(message, SystemMessage))
        )
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content="done"))])

    monkeypatch.setattr(GenericFakeChatModel, "_agenerate", generate)
    monkeypatch.setattr(agent, "_DEFAULT_MODEL", _calling())
    store = InMemoryStore()
    for parameter in ("first_parameter", "second_parameter"):
        catalog = _catalog("lookup", "grant").model_copy(
            update={
                "operations": [
                    OperationSpec(
                        name="lookup",
                        description=f"Uses {parameter}",
                        parameters={
                            "type": "object",
                            "properties": {parameter: {"type": "string"}},
                            "required": [parameter],
                        },
                        grant="grant",
                    )
                ],
                "code_only": ["lookup"],
            }
        )
        await _run(store, parameter, _context("agent", parameter, catalog))

    assert "first_parameter" in prompts[0] and "second_parameter" not in prompts[0]
    assert "second_parameter" in prompts[1] and "first_parameter" not in prompts[1]


async def test_sync_subagent_uses_its_runs_prompt_model_and_tools(
    monkeypatch: pytest.MonkeyPatch, backend_calls: BackendCalls
) -> None:
    observed: list[tuple[str, str, set[str]]] = []

    def bind(
        model: GenericFakeChatModel, tools: list[BaseTool], **kwargs: object
    ) -> GenericFakeChatModel:
        if model.name and model.name.startswith("child-"):
            observed.append((current_run().bearer or "", model.name, {tool.name for tool in tools}))
        return model

    async def generate(
        model: GenericFakeChatModel, messages: list[BaseMessage], **kwargs: object
    ) -> ChatResult:
        if model.name and model.name.startswith("child-"):
            assert any(
                f"Prompt for {model.name}" in message.text
                for message in messages
                if isinstance(message, SystemMessage)
            )
        if any(isinstance(message, ToolMessage) for message in messages):
            reply = AIMessage(content="done")
        elif model.name and model.name.startswith("child-"):
            reply = AIMessage(
                content="",
                tool_calls=[ToolCall(name=f"operation_{model.name[-1]}", args={}, id="child-call")],
            )
        else:
            reply = AIMessage(
                content="",
                tool_calls=[
                    ToolCall(
                        name="task",
                        args={"subagent_type": "general-purpose", "description": "Do the task"},
                        id="task-call",
                    )
                ],
            )
        return ChatResult(generations=[ChatGeneration(message=reply)])

    def build(spec: ModelSpec, **kwargs: object) -> GenericFakeChatModel:
        name = spec.model_id
        model = _calling()
        model.name = name
        return model

    monkeypatch.setattr(GenericFakeChatModel, "bind_tools", bind)
    monkeypatch.setattr(GenericFakeChatModel, "_agenerate", generate)
    monkeypatch.setattr("rt.middleware.build_model", build)
    monkeypatch.setattr("rt.run_tools.build_model", build)
    monkeypatch.setattr(agent, "_DEFAULT_MODEL", _calling())
    store = InMemoryStore()
    for index in range(2):
        catalog = _catalog(f"operation_{index}", f"grant-{index}").model_copy(
            update={
                "models": RunModels(
                    model=ModelSpec(model_id=f"parent-{index}", kwargs={}),
                    subagent_model=ModelSpec(model_id=f"child-{index}", kwargs={}),
                    use_gateway=False,
                ),
                "subagents": [
                    SubagentSpec(
                        name="general-purpose",
                        description="Run the task",
                        system_prompt=f"Prompt for child-{index}",
                        tools=[f"operation_{index}"],
                    )
                ],
            }
        )
        await _run(store, f"subagent-{index}", _context("agent", f"token-{index}", catalog))

    for token, model, tools in observed:
        index = token[-1]
        assert model == f"child-{index}"
        assert f"operation_{index}" in tools
        assert f"operation_{1 - int(index)}" not in tools
    assert {model for _, model, _ in observed} == {"child-0", "child-1"}
    assert [(bearer, operation) for _, bearer, operation in backend_calls if operation] == [
        ("Bearer token-0", "operation_0:grant-0"),
        ("Bearer token-1", "operation_1:grant-1"),
    ]
