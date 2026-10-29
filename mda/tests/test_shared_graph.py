import asyncio
import gc
import threading
import weakref
from collections import OrderedDict
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import agent
import httpx2
import pytest
from deepagents.backends import StateBackend
from deepagents.backends.protocol import ExecuteResponse, SandboxBackendProtocol
from deepagents.middleware.summarization import DEEPAGENTS_DEFAULT_SUMMARY_PROMPT
from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.callbacks import AsyncCallbackManagerForLLMRun
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, BaseMessage, SystemMessage, ToolCall, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables.config import var_child_runnable_config
from langchain_core.tools import BaseTool, tool
from langchain_quickjs._repl import EvalOutcome, _Registry, _ThreadREPL
from langgraph.config import get_config
from langgraph.errors import NodeCancelledError
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt.tool_node import ToolRuntime
from langgraph.store.memory import InMemoryStore
from langgraph_sdk.runtime import _ExecutionRuntime, _ReadRuntime
from langsmith.sandbox import (
    ResourceCreationError,
    ResourceNameConflictError,
    ResourceNotFoundError,
    SandboxAPIError,
    SandboxAuthenticationError,
    SandboxClientError,
)
from managed_deepagents import (
    DeepAgentDefinition,
    ManagedServerRuntime,
    define_deep_agent,
    shared_factory,
)
from managed_deepagents import browser as mda_browser
from managed_deepagents import runtime as mda_runtime
from managed_deepagents.runtime import ManagedRunConfig, compile_managed_agent_async
from managed_deepagents.sandbox import SandboxDefinition
from pydantic import BaseModel, JsonValue
from rt.backend import Run, current_run
from rt.managed_sandbox import definition, opened_sandbox
from rt.run_tools import ModelHandler, RunCodeInterpreter
from runtime_api.catalog import (
    Catalog,
    LoopRules,
    ModelSpec,
    RunModels,
    SandboxCreate,
    SandboxSpec,
    SubagentSpec,
    ToolSpec,
)
from runtime_api.reports import Reports
from runtime_api.tool_search import ResolvedTool, ToolSpecs

_WORKERS = {"general-purpose": "open-swe--general-purpose"}

type BackendCalls = list[tuple[str, str, str | None]]
type Prepared = list[tuple[JsonValue, JsonValue]]
type Browsers = list[tuple[JsonValue, JsonValue]]


def _thread(thread_id: str) -> ManagedRunConfig:
    return {"configurable": {"thread_id": thread_id}}


# Each run's catalog by its token, whose tool specs the fake backend reads to it.
_CATALOGS: dict[str, Catalog] = {}


def _context(graph: str, token: str, catalog: Catalog) -> dict[str, object]:
    """A run as dispatch starts it: its catalog's tools by name alone."""
    _CATALOGS[token] = catalog
    return {
        "graph": graph,
        "__run_token": token,
        "invocation_id": f"invocation-{token}",
        "__catalog": catalog.names_only().model_dump(mode="json"),
    }


def _catalog(operation: str, grant: str, *, snapshot: str | None = None) -> Catalog:
    return Catalog(
        operations=[
            ToolSpec(name=operation, description="An operation.", parameters={}, grant=grant)
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
    """The box each run reported as it prepared, and why it could not open one."""
    return []


@pytest.fixture
def browsers() -> Browsers:
    """Each browser a run reported, and whether the agent had opened it."""
    return []


@pytest.fixture
def backend_calls(
    monkeypatch: pytest.MonkeyPatch, prepared: Prepared, browsers: Browsers
) -> BackendCalls:
    """Every call the runs make on the backend: its endpoint, its bearer and, for an
    operation, the name and grant it was called with."""
    monkeypatch.setenv("OPEN_SWE_BACKEND_URL", "https://backend.invalid")
    monkeypatch.setattr(mda_runtime._SharedDefinitions, "_built", OrderedDict())
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
            prepared.append((json.get("sandbox_id"), json.get("sandbox_error")))
        if hook == "browser":
            browsers.append((json["browser"], json["opened"]))
        if hook == "operation":
            return httpx2.Response(200, request=request, json={"content": "found"})
        if hook == "tool_specs":
            catalog = _CATALOGS[headers["Authorization"].removeprefix("Bearer ")]
            names = json["names"]
            assert isinstance(names, list)
            listed = ToolSpecs(tools=[tool for tool in catalog.operations if tool.name in names])
            return httpx2.Response(200, request=request, json=listed.model_dump(mode="json"))
        if hook == "resolve_tool":
            calls[-1] = (hook, headers["Authorization"], str(json["name"]))
            resolved = ResolvedTool(
                tool=ToolSpec(
                    name="withheld_lookup",
                    description="A tool the catalog leaves out.",
                    parameters={},
                    code_name="withheldLookup",
                    grant="found-grant",
                )
                if json["name"] == "withheldLookup"
                else None
            )
            return httpx2.Response(200, request=request, json=resolved.model_dump(mode="json"))
        if hook == "reports":
            taken = {"replies": [{} for _ in Reports.model_validate(json).items]}
            return httpx2.Response(200, request=request, json=taken)
        return httpx2.Response(200, request=request, json={"files": []} if hook == "skills" else {})

    client = MagicMock(is_closed=False)
    client.post = AsyncMock(side_effect=post)
    monkeypatch.setattr("rt.backend.httpx2.AsyncClient", lambda **kwargs: client)
    return calls


async def _run(
    store: InMemoryStore,
    thread_id: str,
    context: dict[str, object],
    messages: list[tuple[str, str]] | None = None,
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
        {"messages": messages or [("user", "Review it")]},
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
    monkeypatch.setattr(mda_runtime._SharedDefinitions, "_built", OrderedDict())
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
    assert prepared == [("box-thread-1", None), ("box-thread-2", None)]
    assert opened == {("thread-1", "a"), ("thread-2", "b")}


async def test_a_run_retries_a_box_the_sandbox_service_failed_to_open_and_reports_why_it_gave_up(
    monkeypatch: pytest.MonkeyPatch, backend_calls: BackendCalls, prepared: Prepared
) -> None:
    """A 503 while opening or creating the box is retried until it opens; a refusal that will not
    change is not, and the backend hears its cause without the secret in it."""
    request = httpx2.Request("POST", "https://sandbox.invalid/boxes")
    response = httpx2.Response(503, request=request)
    unavailable = SandboxClientError("Service Unavailable")
    unavailable.__cause__ = httpx2.HTTPStatusError("503", request=request, response=response)
    unscheduled = ResourceCreationError("No capacity", error_type="Unschedulable")
    unscheduled.__cause__ = httpx2.HTTPStatusError("503", request=request, response=response)
    failures: dict[object, list[Exception]] = {
        "thread-1": [unavailable, unscheduled],
        "thread-2": [SandboxAuthenticationError("Invalid api_key=lsv2-secret")],
    }
    attempts: list[object] = []

    def resolve(
        sandbox: SandboxDefinition, config: ManagedRunConfig | None, *args: object
    ) -> StateBackend:
        thread_id = ((config or {}).get("configurable") or {}).get("thread_id")
        attempts.append(thread_id)
        if failures[thread_id]:
            raise failures[thread_id].pop(0)
        box = MagicMock(spec=StateBackend)
        box.id = f"box-{thread_id}"
        return box

    monkeypatch.setattr(mda_runtime, "_resolve_managed_sandbox", resolve)
    monkeypatch.setattr("rt.managed_sandbox.asyncio.sleep", AsyncMock())
    answers = iter([AIMessage(content="done"), AIMessage(content="done")])
    monkeypatch.setattr(agent, "_DEFAULT_MODEL", GenericFakeChatModel(messages=answers))
    store = InMemoryStore()

    for thread_id, token in (("thread-1", "token-1"), ("thread-2", "token-2")):
        catalog = _catalog("web_search", token, snapshot="a")
        await _run(store, thread_id, _context("agent", token, catalog))

    assert attempts == ["thread-1", "thread-1", "thread-1", "thread-2"]
    assert prepared == [
        ("box-thread-1", None),
        (None, "SandboxAuthenticationError: Invalid api_key=[redacted]"),
    ]


@pytest.mark.parametrize("failure", ["lookup", "restart", "unusable", "conflict"])
def test_thread_sandbox_failures_never_create_a_second_workspace(
    monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    monkeypatch.setenv("MDA_DEPLOYMENT_NAME", "open-swe-mda-prod")
    monkeypatch.delenv("LANGSMITH_DEPLOYMENT_NAME", raising=False)
    client = MagicMock()
    existing = MagicMock(status="stopped" if failure == "restart" else "failed")
    client.get_sandbox.return_value = existing
    client.start_sandbox.side_effect = SandboxAPIError("HTTP 503")
    if failure == "lookup":
        client.get_sandbox.side_effect = SandboxAPIError("HTTP 503")
    elif failure == "conflict":
        client.get_sandbox.side_effect = ResourceNotFoundError("not found")
        client.create_sandbox.side_effect = ResourceNameConflictError("name already exists")
    monkeypatch.setattr(mda_runtime, "_sandbox_client_for", lambda sandbox: client)
    spec = _catalog("lookup", "grant", snapshot="snapshot").sandbox
    assert spec is not None
    error = (
        ResourceNameConflictError
        if failure == "conflict"
        else RuntimeError
        if failure == "unusable"
        else SandboxAPIError
    )

    with pytest.raises(error):
        mda_runtime._create_langsmith_sandbox(
            definition(spec.create, None), scope_key="thread:thread-1"
        )

    assert len(client.create_sandbox.call_args_list) == (1 if failure == "conflict" else 0)


@pytest.mark.parametrize("replaced", [False, True])
def test_losing_a_thread_sandbox_only_reconnects_to_an_existing_replacement(
    monkeypatch: pytest.MonkeyPatch, replaced: bool
) -> None:
    spec = _catalog("lookup", "grant", snapshot="snapshot").sandbox
    assert spec is not None
    sandbox = definition(spec.create, None)
    original = MagicMock(spec=SandboxBackendProtocol)
    original.execute.side_effect = ResourceNotFoundError("sandbox is gone")
    entry = mda_runtime._ManagedSandboxEntry(original, host_name="original", sandbox_id="old-id")
    monkeypatch.setattr(mda_runtime, "_managed_sandboxes", {"thread:thread-1": entry})
    client = MagicMock()
    replacement = MagicMock(name="original", status="running")
    replacement.name = "original"
    replacement.id = "new-id"
    resolved = MagicMock(spec=SandboxBackendProtocol)
    monkeypatch.setattr(mda_runtime, "LangSmithSandbox", lambda **kwargs: resolved)
    if replaced:
        client.get_sandbox.return_value = replacement
    else:
        client.get_sandbox.side_effect = ResourceNotFoundError("sandbox is gone")
    monkeypatch.setattr(mda_runtime, "_sandbox_client_for", lambda sandbox: client)
    backend = mda_runtime._managed_sandbox_backend(sandbox, _thread("thread-1"))

    with pytest.raises(ResourceNotFoundError, match="sandbox is gone"):
        backend.execute("pwd")

    assert backend._backend() is (resolved if replaced else original)
    assert entry.backend is original
    client.create_sandbox.assert_not_called()


@pytest.mark.parametrize("missing", [False, True])
def test_persisted_sandbox_binding_overrides_another_replicas_canonical_cache(
    monkeypatch: pytest.MonkeyPatch, missing: bool
) -> None:
    spec = _catalog("lookup", "grant", snapshot="snapshot").sandbox
    assert spec is not None
    bound_name = "original-legacy-suffix"
    sandbox = definition(spec.create, None, bound_name)
    stale = MagicMock(spec=SandboxBackendProtocol)
    monkeypatch.setattr(
        mda_runtime,
        "_managed_sandboxes",
        {"thread:thread-1": mda_runtime._ManagedSandboxEntry(stale, host_name="original")},
    )
    client = MagicMock()
    handle = MagicMock(status="running")
    handle.name = bound_name
    handle.id = "bound-id"
    if missing:
        client.get_sandbox.side_effect = ResourceNotFoundError("not found")
    else:
        client.get_sandbox.return_value = handle
    resolved = MagicMock(spec=SandboxBackendProtocol)
    monkeypatch.setattr(mda_runtime, "_sandbox_client_for", lambda sandbox: client)
    monkeypatch.setattr(mda_runtime, "LangSmithSandbox", lambda **kwargs: resolved)

    if missing:
        with pytest.raises(RuntimeError, match="bound sandbox.*no longer exists"):
            mda_runtime._resolve_managed_sandbox(sandbox, _thread("thread-1"))
    else:
        assert mda_runtime._resolve_managed_sandbox(sandbox, _thread("thread-1")) is resolved
    assert all(call.args == (bound_name,) for call in client.get_sandbox.call_args_list)
    client.create_sandbox.assert_not_called()
    client.update_sandbox.assert_not_called()
    client.stop_sandbox.assert_not_called()
    client.delete_sandbox.assert_not_called()


@pytest.mark.parametrize("reappeared", [False, True])
async def test_deleted_bound_workspace_recovers_under_its_name_and_reports_loss(
    monkeypatch: pytest.MonkeyPatch, reappeared: bool
) -> None:
    monkeypatch.setenv("MDA_DEPLOYMENT_NAME", "open-swe-mda-prod")
    spec = _catalog("lookup", "grant", snapshot="snapshot").sandbox
    assert spec is not None
    name = "original-legacy-suffix"
    sandbox = definition(spec.create, None, name)
    monkeypatch.setattr(mda_runtime, "_managed_sandboxes", {})
    client = MagicMock()
    client.get_sandbox.side_effect = (
        [ResourceNotFoundError("deleted"), MagicMock(status="running")]
        if reappeared
        else ResourceNotFoundError("deleted")
    )
    fresh = MagicMock(status="running")
    fresh.name = name
    fresh.id = "fresh-id"
    client.create_sandbox.return_value = fresh
    resolved = MagicMock(spec=SandboxBackendProtocol)
    resolved.id = name
    monkeypatch.setattr(mda_runtime, "_sandbox_client_for", lambda sandbox: client)
    monkeypatch.setattr(mda_runtime, "LangSmithSandbox", lambda **kwargs: resolved)
    backend = mda_runtime._managed_sandbox_backend(sandbox, _thread("thread-1"))

    opened = await opened_sandbox(MagicMock(backend=backend))

    if reappeared:
        assert opened.error is not None and "refusing automatic replacement" in opened.error
        assert opened.workspace_lost is False
        client.create_sandbox.assert_not_called()
    else:
        assert opened.sandbox_id == name
        assert opened.workspace_lost is True
        assert opened.error is None
        assert client.create_sandbox.call_args.kwargs["name"] == name
    client.update_sandbox.assert_not_called()
    client.stop_sandbox.assert_not_called()
    client.delete_sandbox.assert_not_called()


@pytest.mark.parametrize("bound", [False, True])
async def test_explicit_recreation_can_replace_a_failed_uncached_sandbox(
    monkeypatch: pytest.MonkeyPatch, bound: bool
) -> None:
    monkeypatch.setenv("MDA_DEPLOYMENT_NAME", "open-swe-mda-prod")
    monkeypatch.delenv("LANGSMITH_DEPLOYMENT_NAME", raising=False)
    spec = _catalog("lookup", "grant", snapshot="snapshot").sandbox
    assert spec is not None
    bound_name = (
        "original-legacy-suffix"
        if bound
        else mda_runtime._managed_sandbox_name("open-swe-mda-prod", "thread:thread-1")
    )
    sandbox = definition(spec.create, None, bound_name if bound else None)
    monkeypatch.setattr(mda_runtime, "_managed_sandboxes", {})
    client = MagicMock()
    client.get_sandbox.return_value = MagicMock(status="failed")
    fresh = MagicMock(status="running")
    fresh.name = bound_name
    fresh.id = "fresh-id"
    client.create_sandbox.return_value = fresh
    resolved = MagicMock(spec=SandboxBackendProtocol)
    monkeypatch.setattr(mda_runtime, "_sandbox_client_for", lambda sandbox: client)
    monkeypatch.setattr(mda_runtime, "LangSmithSandbox", lambda **kwargs: resolved)
    backend = mda_runtime._managed_sandbox_backend(sandbox, _thread("thread-1"))

    with pytest.raises(RuntimeError, match="not usable"):
        await backend.aopen()
    client.create_sandbox.assert_not_called()
    replacement = await backend.areplace()

    assert replacement.new_name == bound_name
    assert replacement.old_name.startswith(f"{bound_name}-old-")
    assert client.update_sandbox.call_args.args == (bound_name,)
    assert client.create_sandbox.call_args.kwargs["name"] == bound_name
    assert await backend.aopen() == resolved.id
    client.delete_sandbox.assert_not_called()


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
        for middleware in agent._parts("agent").middleware
        if isinstance(middleware, RunCodeInterpreter)
    )
    assert not interpreter._registry._slots
    assert not interpreter._ptc_tools_by_slot


async def test_code_mode_calls_tools_its_catalog_leaves_out(
    monkeypatch: pytest.MonkeyPatch, backend_calls: BackendCalls
) -> None:
    """The backend names what ``tools.<name>`` is, with the run's grant; a name it has none for throws."""
    code = "await tools.withheldLookup({}); await tools.withheldLookup({}); await tools.notOne({})"
    seen: list[BaseMessage] = []
    generate = GenericFakeChatModel._agenerate

    async def answering(
        model: GenericFakeChatModel,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: object,
    ) -> ChatResult:
        seen[:] = messages
        return await generate(model, messages, stop, run_manager)

    monkeypatch.setattr(GenericFakeChatModel, "_agenerate", answering)
    replies = [
        AIMessage(content="", tool_calls=[ToolCall(name="eval", args={"code": code}, id="eval")]),
        # Called directly too, by the name a search gave, and by one that is no tool.
        AIMessage(content="", tool_calls=[ToolCall(name="withheldLookup", args={}, id="direct")]),
        AIMessage(content="", tool_calls=[ToolCall(name="noTool", args={}, id="no-tool")]),
        AIMessage(content="done"),
    ]
    monkeypatch.setattr(agent, "_DEFAULT_MODEL", GenericFakeChatModel(messages=iter(replies)))
    catalog = _catalog("lookup", "grant").model_copy(update={"search_tools": True})

    await _run(InMemoryStore(), "withheld", _context("agent", "token", catalog))

    [evaluated] = [m for m in seen if isinstance(m, ToolMessage) and m.tool_call_id == "eval"]
    # The run goes on past a call of no tool, as the tool node answers it.
    [missing] = [m for m in seen if isinstance(m, ToolMessage) and m.tool_call_id == "no-tool"]
    assert missing.status == "error" and "noTool" in missing.text
    assert '<error type="TypeError">notOne is not one of this run' in evaluated.text
    called = [(hook, detail) for hook, _, detail in backend_calls if detail]
    # Its context names its own tools, whose specs it reads once.
    assert [hook for hook, _, _ in backend_calls].count("tool_specs") == 1
    # The rest resolved once, each called with the grant the backend gave.
    assert called == [
        ("resolve_tool", "withheldLookup"),
        ("operation", "withheld_lookup:found-grant"),
        ("operation", "withheld_lookup:found-grant"),
        ("resolve_tool", "notOne"),
        ("operation", "withheld_lookup:found-grant"),
        ("resolve_tool", "noTool"),
    ]


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
        for middleware in agent._parts("agent").middleware
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
                    ToolSpec(
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
    monkeypatch.setattr(agent, "build_model", build)
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


async def test_each_run_compacts_a_long_conversation_with_its_own_model(
    monkeypatch: pytest.MonkeyPatch, backend_calls: BackendCalls
) -> None:
    """deepagents summarizes with the model its graph is built with, so a graph built with
    a placeholder summarized every run's conversation with that placeholder."""
    summarized_by: list[str | None] = []

    async def generate(
        model: GenericFakeChatModel, messages: list[BaseMessage], **kwargs: object
    ) -> ChatResult:
        if messages[0].text.startswith(DEEPAGENTS_DEFAULT_SUMMARY_PROMPT[:40]):
            summarized_by.append(model.name)
            reply = AIMessage(content="summary")
        else:
            reply = AIMessage(content="done")
        return ChatResult(generations=[ChatGeneration(message=reply)])

    def build(spec: ModelSpec, **kwargs: object) -> GenericFakeChatModel:
        return GenericFakeChatModel(
            messages=iter([]), name=spec.model_id, profile={"max_input_tokens": 40_000}
        )

    monkeypatch.setattr(GenericFakeChatModel, "_agenerate", generate)
    monkeypatch.setattr(agent, "build_model", build)
    monkeypatch.setattr("rt.middleware.build_model", build)
    monkeypatch.setattr("rt.run_tools.build_model", build)
    monkeypatch.setattr(
        agent,
        "_DEFAULT_MODEL",
        GenericFakeChatModel(
            messages=iter([]), name="placeholder", profile={"max_input_tokens": 40_000}
        ),
    )
    store = InMemoryStore()
    history: list[tuple[str, str]] = [("user", "history " * 600) for _ in range(40)]

    def catalog(model_id: str) -> Catalog:
        return _catalog("web_search", "grant").model_copy(
            update={
                "models": RunModels(
                    model=ModelSpec(model_id=model_id, kwargs={}),
                    subagent_model=ModelSpec(model_id=f"{model_id}-child", kwargs={}),
                    use_gateway=False,
                )
            }
        )

    first = await _run(
        store, "openai", _context("agent", "token-0", catalog("openai:gpt-test")), history
    )
    second = await _run(
        store, "anthropic", _context("agent", "token-1", catalog("anthropic:test")), history
    )
    again = await _run(
        store, "openai-again", _context("agent", "token-2", catalog("openai:gpt-test")), history
    )

    assert summarized_by == ["openai:gpt-test", "anthropic:test", "openai:gpt-test"]
    # One graph per model, shared by every run on it.
    assert again.nodes["model"] is first.nodes["model"]
    assert second.nodes["model"] is not first.nodes["model"]


async def test_a_shared_factory_builds_once_per_inputs_and_never_sees_the_run(
    backend_calls: BackendCalls,
) -> None:
    """The first run on some inputs builds the graph every later run on them shares, so
    nothing of that run may reach the build: only the inputs do."""
    builds: list[tuple[str, bool]] = []

    class Inputs(BaseModel):
        graph: str

    async def inputs(runtime: ManagedServerRuntime) -> Inputs:
        execution = runtime.execution_runtime
        context = execution.context if execution is not None else None
        return Inputs(graph=str((context or {}).get("graph")))

    @shared_factory(inputs)
    def probe(built_from: Inputs) -> DeepAgentDefinition:
        try:
            get_config()
            sees_run = True
        except RuntimeError:
            sees_run = False
        builds.append((built_from.graph, sees_run))
        return define_deep_agent(name="probe", model=GenericFakeChatModel(messages=iter([])))

    async def compile_for(graph: str, thread_id: str) -> CompiledStateGraph:
        context: dict[str, object] = {"graph": graph}
        # As a run's own config is in progress around the factory.
        token = var_child_runnable_config.set(_thread(thread_id))
        try:
            return await compile_managed_agent_async(
                probe,
                _thread(thread_id),
                runtime=_ExecutionRuntime(
                    access_context="threads.create_run", store=InMemoryStore(), context=context
                ),
                report_telemetry=False,
            )
        finally:
            var_child_runnable_config.reset(token)

    first = await compile_for("agent", "thread-1")
    second = await compile_for("agent", "thread-2")
    other = await compile_for("reviewer", "thread-3")

    assert builds == [("agent", False), ("reviewer", False)]
    assert second.nodes["model"] is first.nodes["model"]
    assert other.nodes["model"] is not first.nodes["model"]


async def test_only_the_coding_agent_browses_and_never_hands_the_browser_over(
    monkeypatch: pytest.MonkeyPatch, backend_calls: BackendCalls, browsers: Browsers, tmp_path: Path
) -> None:
    """The coding agent gets the browser's tools and instructions but no browser_handoff,
    and its first browse reports the browser it named when the run started; the
    reviewer has no browser."""
    offered: dict[str, set[str]] = {}
    instructed: dict[str, bool] = {}

    def bind(
        model: GenericFakeChatModel, tools: list[BaseTool], **kwargs: object
    ) -> GenericFakeChatModel:
        offered[current_run().bearer or ""] = {tool.name for tool in tools}
        return model

    async def generate(
        model: GenericFakeChatModel, messages: list[BaseMessage], **kwargs: object
    ) -> ChatResult:
        bearer = current_run().bearer or ""
        system = "\n".join(m.text for m in messages if isinstance(m, SystemMessage))
        instructed[bearer] = "browser_snapshot" in system
        browsed = any(isinstance(message, ToolMessage) for message in messages)
        if "browser_snapshot" in offered[bearer] and not browsed:
            reply = AIMessage(
                content="", tool_calls=[ToolCall(name="browser_snapshot", args={}, id="look")]
            )
        else:
            reply = AIMessage(content="done")
        return ChatResult(generations=[ChatGeneration(message=reply)])

    async def open_browser(key: str, window: str, idle_ttl_seconds: int) -> mda_browser._Session:
        tools = MagicMock(snapshot=AsyncMock(return_value="a page"))
        name = mda_browser._browser_name(key)
        return mda_browser._Session(window, MagicMock(), tools, str(tmp_path), name)

    monkeypatch.setattr(GenericFakeChatModel, "bind_tools", bind)
    monkeypatch.setattr(GenericFakeChatModel, "_agenerate", generate)
    monkeypatch.setattr(agent, "_DEFAULT_MODEL", _calling())
    monkeypatch.setattr(mda_browser, "_open", open_browser)
    monkeypatch.setattr(mda_browser, "_RELEASE_AFTER_S", 0)
    store = InMemoryStore()
    await _run(store, "reviewed", _context("reviewer", "reviewer", _catalog("lookup", "grant")))
    await _run(store, "browsed", _context("agent", "agent", _catalog("lookup", "grant")))
    await asyncio.sleep(0)

    tools = {"browser_run", "browser_snapshot", "browser_screenshot"}
    assert tools <= offered["agent"] and "browser_handoff" not in offered["agent"]
    assert not {name for name in offered["reviewer"] if name.startswith("browser_")}
    assert instructed == {"agent": True, "reviewer": False}
    browser = mda_browser._browser_name("thread:browsed")
    assert browsers == [(browser, False), (browser, True)]


@pytest.mark.parametrize("cancel", ["none", "task", "masked"])
async def test_closed_eval_recovers_without_replay_but_run_cancellation_is_terminal(
    monkeypatch: pytest.MonkeyPatch, backend_calls: BackendCalls, cancel: str
) -> None:
    entered = asyncio.Event()
    original = _ThreadREPL.eval_async
    attempts = 0
    observed: list[ToolMessage] = []

    async def evaluate(
        repl: _ThreadREPL,
        code: str,
        *,
        outer_runtime: ToolRuntime | None = None,
        outer_loop: asyncio.AbstractEventLoop | None = None,
    ) -> EvalOutcome:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            entered.set()
            if cancel != "none":
                try:
                    if cancel == "masked":
                        raise BaseExceptionGroup("worker cancelled", [asyncio.CancelledError()])
                    await asyncio.Event().wait()
                except BaseException as exc:
                    raise ValueError("already closed") from exc
            await repl.aclose()
            raise ValueError("engine closed with different wording")
        return await original(repl, code, outer_runtime=outer_runtime, outer_loop=outer_loop)

    def replies() -> Iterator[AIMessage]:
        for index, code in enumerate(
            [
                "await tools.lookup({})",
                "await tools.lookup({})",
                "throw new Error('ordinary eval failure')",
            ]
        ):
            yield AIMessage(
                content="",
                tool_calls=[ToolCall(name="eval", args={"code": code}, id=f"eval-{index}")],
            )
        yield AIMessage(content="done")

    original_generate = GenericFakeChatModel._agenerate

    async def generate(
        model: GenericFakeChatModel, messages: list[BaseMessage], **kwargs: object
    ) -> ChatResult:
        observed[:] = [message for message in messages if isinstance(message, ToolMessage)]
        return await original_generate(model, messages)

    monkeypatch.setattr(_ThreadREPL, "eval_async", evaluate)
    monkeypatch.setattr(GenericFakeChatModel, "_agenerate", generate)
    monkeypatch.setattr(agent, "_DEFAULT_MODEL", GenericFakeChatModel(messages=replies()))
    catalog = _catalog("lookup", "grant").model_copy(update={"code_only": ["lookup"]})
    task = asyncio.create_task(_run(InMemoryStore(), "closed", _context("agent", "token", catalog)))
    await asyncio.wait_for(entered.wait(), 10)
    if cancel != "none":
        if cancel == "task":
            task.cancel()
        with pytest.raises(asyncio.CancelledError if cancel == "task" else NodeCancelledError):
            await task
        assert not observed
        assert attempts == 1
    else:
        await task
        assert observed[0].status == "error"
        assert "found" in observed[1].text
        assert attempts == 3
        assert "ordinary eval failure" in observed[2].text
        assert sum(operation is not None for _, _, operation in backend_calls) == 1
    interpreter = next(
        item for item in agent._parts("agent").middleware if isinstance(item, RunCodeInterpreter)
    )
    assert not interpreter._registry._slots
    assert not interpreter._slots


@pytest.mark.parametrize("failure", ["before_agent", "before_model", "between_steps"])
async def test_interpreter_cleanup_survives_failures_outside_its_hooks(
    monkeypatch: pytest.MonkeyPatch, backend_calls: BackendCalls, failure: str
) -> None:
    from rt.browser import ReportedBrowserMiddleware
    from rt.middleware import BackendRunMiddleware

    interpreter = next(
        item for item in agent._parts("agent").middleware if isinstance(item, RunCodeInterpreter)
    )
    original = interpreter.abefore_agent

    async def prepare(*args: object, **kwargs: object) -> dict[str, object] | None:
        update = await original(*args, **kwargs)
        assert update is not None
        interpreter._registry.get(str(update["_quickjs_slot_id"]))
        return update

    async def later_setup(*args: object, **kwargs: object) -> None:
        raise RuntimeError("later setup failed")

    async def read_inbox(*args: object, **kwargs: object) -> None:
        raise RuntimeError("inbox failed")

    monkeypatch.setattr(interpreter, "abefore_agent", prepare)
    if failure == "before_agent":
        monkeypatch.setattr(ReportedBrowserMiddleware, "abefore_agent", later_setup)
    if failure == "before_model":
        monkeypatch.setattr(BackendRunMiddleware, "_read_inbox", read_inbox)
    monkeypatch.setattr(agent, "_DEFAULT_MODEL", _calling())
    catalog = _catalog("lookup", "grant").model_copy(update={"code_only": ["lookup"]})
    context = _context("agent", "cleanup", catalog)
    if failure == "between_steps":
        graph = await compile_managed_agent_async(
            agent.agent,
            _thread("cleanup"),
            runtime=_ExecutionRuntime(
                access_context="threads.create_run", store=InMemoryStore(), context=context
            ),
            report_telemetry=False,
            async_subagent_graphs=_WORKERS,
        )
        stream = graph.astream(
            {"messages": [("user", "hello")]},
            _thread("cleanup"),
            context=context,  # ty: ignore[invalid-argument-type]
            stream_mode="updates",
        )
        async for _ in stream:
            if interpreter._registry._slots:
                break
        assert interpreter._registry._slots
        await stream.aclose()
    else:
        with pytest.raises(RuntimeError, match="later setup failed|inbox failed"):
            await _run(InMemoryStore(), "cleanup", context)
    assert not interpreter._registry._slots
    assert not interpreter._slots
    assert not interpreter._ptc_tools_by_slot
    assert not interpreter._ptc_prompt_by_slot


async def test_recovered_model_failure_preserves_interpreter_state(
    monkeypatch: pytest.MonkeyPatch, backend_calls: BackendCalls
) -> None:
    class RecoverModel(AgentMiddleware):
        async def awrap_model_call(
            self, request: ModelRequest, handler: ModelHandler
        ) -> ModelResponse:
            try:
                return await handler(request)
            except RuntimeError as exc:
                if str(exc) != "recoverable model failure":
                    raise
                return await handler(request)

    parts = agent._parts("agent")
    patched = replace(parts, middleware=[RecoverModel(), *parts.middleware])
    patched = replace(patched, worker_middleware=patched.middleware)
    monkeypatch.setattr(agent, "_parts", lambda graph: patched)
    attempts = 0
    original = GenericFakeChatModel._agenerate

    async def generate(
        model: GenericFakeChatModel, messages: list[BaseMessage], **kwargs: object
    ) -> ChatResult:
        nonlocal attempts
        attempts += 1
        if attempts == 2:
            raise RuntimeError("recoverable model failure")
        if attempts == 4:
            assert isinstance(messages[-1], ToolMessage)
            assert "42" in messages[-1].text
        return await original(model, messages)

    monkeypatch.setattr(GenericFakeChatModel, "_agenerate", generate)
    monkeypatch.setattr(
        agent,
        "_DEFAULT_MODEL",
        GenericFakeChatModel(
            messages=iter(
                [
                    AIMessage(
                        content="",
                        tool_calls=[ToolCall(name="eval", args={"code": code}, id=f"eval-{i}")],
                    )
                    for i, code in enumerate(["var retained = 42", "retained"])
                ]
                + [AIMessage(content="done")]
            )
        ),
    )
    await _run(
        InMemoryStore(),
        "recover",
        _context(
            "agent",
            "recover",
            _catalog("lookup", "grant").model_copy(update={"code_only": ["lookup"]}),
        ),
    )


async def test_cancelling_an_eval_its_worker_has_not_started_returns_promptly() -> None:
    registry = _Registry(
        memory_limit=64 << 20, timeout=5, capture_console=True, max_stdout_chars=1000
    )
    repl = registry.get("busy")
    worker_loop = registry._slots["busy"].worker._loop
    assert worker_loop is not None
    started = threading.Event()
    release = threading.Event()

    def block() -> None:
        started.set()
        release.wait(5)

    worker_loop.call_soon_threadsafe(block)
    assert await asyncio.to_thread(started.wait, 5)
    try:
        evaluation = asyncio.create_task(repl.eval_async("globalThis.ran = true"))
        await asyncio.sleep(0)
        evaluation.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(evaluation, 1)
    finally:
        release.set()
    assert (await repl.eval_async("typeof globalThis.ran")).result == "undefined"
    await registry.aevict("busy")


async def test_a_tool_result_the_interpreter_cannot_take_fails_the_eval_not_the_run() -> None:
    nested: list[object] = []
    for _ in range(200):
        nested = [nested]

    @tool
    def nest() -> list[object]:
        """Return a result nested deeper than the interpreter marshals."""
        return nested

    registry = _Registry(
        memory_limit=64 << 20, timeout=5, capture_console=True, max_stdout_chars=1000
    )
    repl = registry.get("nest")
    repl.install_tools([nest])
    try:
        failed = await repl.eval_async("globalThis.kept = 1; await tools.nest({})")
        assert failed.error_type == "MarshalError"
        assert (await repl.eval_async("kept")).result == "1"
    finally:
        await registry.aevict("nest")
