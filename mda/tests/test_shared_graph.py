import asyncio
import gc
import json
import threading
import weakref
from collections import OrderedDict
from collections.abc import Iterator, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Final, Literal, cast
from unittest.mock import AsyncMock, MagicMock

import agent
import httpx
import httpx2
import pytest
from deepagents.backends import StateBackend
from deepagents.backends.protocol import ExecuteResponse, SandboxBackendProtocol
from deepagents.middleware.summarization import DEEPAGENTS_DEFAULT_SUMMARY_PROMPT
from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.callbacks import AsyncCallbackManagerForLLMRun
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import (
    AIMessage,
    AnyMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolCall,
    ToolMessage,
)
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables.config import var_child_runnable_config
from langchain_core.tools import BaseTool, tool
from langchain_core.tracers.langchain import LangChainTracer
from langchain_quickjs._repl import _Slot, _SlotConfig
from langchain_quickjs.middleware import REPLState
from langgraph.config import get_config
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.runtime import Runtime
from langgraph.store.memory import InMemoryStore
from langgraph_sdk.auth.types import AuthContext, BaseUser, ThreadsCreate, ThreadsRead
from langgraph_sdk.runtime import _ExecutionRuntime, _ReadRuntime
from langsmith import Client, tracing_context
from langsmith.run_trees import RunTree
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
    cached_agent,
    define_deep_agent,
)
from managed_deepagents import _identity_auth as mda_identity_auth
from managed_deepagents import _sandbox_proxy as mda_sandbox_proxy
from managed_deepagents import browser as mda_browser
from managed_deepagents import runtime as mda_runtime
from managed_deepagents.runtime import ManagedRunConfig, compile_managed_agent_async
from managed_deepagents.sandbox import SandboxDefinition, SandboxMissingError
from pydantic import BaseModel, JsonValue
from rt import run_tools, search_tools
from rt.backend import Run, current_run
from rt.managed_sandbox import (
    EXECUTE_TIMEOUT_SECONDS,
    definition,
    opened_sandbox,
)
from rt.outbox import Outbox
from rt.run_tools import ModelHandler, RunCodeInterpreter
from rt.sandbox_setup import SandboxUnavailableError
from runtime_api.catalog import (
    BrowserSpec,
    Catalog,
    LoopRules,
    ModelSpec,
    RunModels,
    SandboxCreate,
    SandboxSpec,
    SubagentSpec,
    ToolSpec,
)
from runtime_api.endpoint import Endpoint
from runtime_api.loop import SANDBOX_UNREACHABLE
from runtime_api.operations import RunRequest
from runtime_api.reports import Reports
from runtime_api.tool_search import KeptTools, ResolvedTool, ToolSpecs

_WORKERS = {"general-purpose": "open-swe--general-purpose"}
# The proxy config the backend works out for a sandbox, which MDA writes there.
_PROXY_CONFIG: dict[str, JsonValue] = {
    "rules": [{"name": "egress", "match_hosts": ["internal.invalid"], "headers": []}],
    "callbacks": [
        {"match_hosts": ["github.com"], "url": "https://backend.invalid/cb", "ttl_seconds": 300}
    ],
}

type BackendCalls = list[tuple[str, str, str | None]]
type Prepared = list[tuple[JsonValue, JsonValue]]
type Bound = list[tuple[JsonValue, JsonValue]]
type Browsers = list[tuple[JsonValue, JsonValue]]


def _thread(thread_id: str) -> ManagedRunConfig:
    return {"configurable": {"thread_id": thread_id}}


# Each run's catalog by its token, whose tool specs the fake backend reads to it.
_CATALOGS: dict[str, Catalog] = {}
# What the fake backend's preparation writes to each run's state.
_PREPARE_UPDATE: dict[str, JsonValue] = {}
# What the fake backend answers a run that reports its sandbox behind its preparation.
_BIND_ANSWER: dict[str, JsonValue] = {}


def _context(graph: str, token: str, catalog: Catalog) -> dict[str, object]:
    """A run as dispatch starts it: its catalog's tools by name alone."""
    _CATALOGS[token] = catalog
    return {
        "graph": graph,
        "origin": catalog.trace_metadata.get("origin", "automation"),
        "__run_token": token,
        "invocation_id": f"invocation-{token}",
        "__catalog": catalog.names_only().model_dump(mode="json"),
    }


def _live_interpreters(interpreter: RunCodeInterpreter) -> list[_Slot]:
    """The interpreter slots whose JavaScript context a run left open."""
    return [slot for slot in interpreter._registry._slots.values() if slot._context is not None]


def _catalog(
    operation: str,
    grant: str,
    *,
    snapshot: str | None = None,
    bind_after_prepare: bool = False,
    external: bool = False,
) -> Catalog:
    return Catalog(
        operations=[
            ToolSpec(name=operation, description="An operation.", parameters={}, grant=grant)
        ],
        subagents=[SubagentSpec(name="reviewer", description="Reviews.", system_prompt="")],
        skill_sources=["/skills/org/"],
        loop=LoopRules(),
        sandbox=SandboxSpec(
            create=SandboxCreate(
                snapshot_id=snapshot,
                fs_capacity_bytes=1,
                idle_ttl_seconds=60,
                delete_after_stop_seconds=60,
            ),
            bind_after_prepare=bind_after_prepare,
            external=external,
            # An eval harness's sandbox already exists, under the name it attached.
            sandbox_id="sandbox-thread-1" if external else None,
            proxy_config=None if external else _PROXY_CONFIG,
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
    """The sandbox each run reported as it prepared, and why it could not open one."""
    return []


@pytest.fixture
def bound() -> Bound:
    """The sandbox each run reported behind its preparation, and why it could not open one."""
    return []


@pytest.fixture
def browsers() -> Browsers:
    """Each browser a run reported, and whether the agent had opened it."""
    return []


@pytest.fixture
def operation_contents() -> dict[str, str]:
    """What each operation answers the model, by its name; any other answers "found"."""
    return {}


@pytest.fixture
def backend_calls(
    monkeypatch: pytest.MonkeyPatch,
    prepared: Prepared,
    bound: Bound,
    browsers: Browsers,
    operation_contents: dict[str, str],
) -> BackendCalls:
    """Every call the runs make on the backend: its endpoint, its bearer and, for an
    operation, the name and grant it was called with."""
    monkeypatch.setenv("OPEN_SWE_BACKEND_URL", "https://backend.invalid")
    monkeypatch.setattr(mda_runtime._CachedDefinitions, "_built", OrderedDict())
    monkeypatch.setattr(mda_runtime._CachedGraphs, "_built", OrderedDict())
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
            return httpx2.Response(200, request=request, json={"update": _PREPARE_UPDATE})
        if hook == "bind_sandbox":
            bound.append((json.get("sandbox_id"), json.get("sandbox_error")))
            return httpx2.Response(200, request=request, json=_BIND_ANSWER)
        if hook == "browser":
            browsers.append((json["browser"], json["opened"]))
        if hook == "operation":
            content = operation_contents.get(str(json["name"]), "found")
            return httpx2.Response(200, request=request, json={"content": content})
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
        if hook == "tool_set":
            digest = _CATALOGS[headers["Authorization"].removeprefix("Bearer ")].tool_set
            kept = KeptTools(
                digest=str(digest),
                tools=[
                    ToolSpec(
                        name="withheld_lookup",
                        description="Look up what the catalog leaves out.",
                        parameters={},
                        code_name="withheldLookup",
                    ),
                    ToolSpec(name="load_integration_tools", description="Load.", parameters={}),
                ],
                excluded=["load_integration_tools"],
            )
            return httpx2.Response(200, request=request, json=kept.model_dump(mode="json"))
        if hook == "reports":
            taken = {"replies": [{} for _ in Reports.model_validate(json).items]}
            return httpx2.Response(200, request=request, json=taken)
        return httpx2.Response(200, request=request, json={"files": []} if hook == "skills" else {})

    client = MagicMock(is_closed=False)
    client.post = AsyncMock(side_effect=post)
    monkeypatch.setattr("rt.backend.httpx2.AsyncClient", lambda **kwargs: client)

    # Each thread's sandbox, which every run works in; a test that looks at it fakes its own.
    def claim(sandbox: SandboxDefinition, config: ManagedRunConfig | None, *args: object) -> str:
        return f"sandbox-{((config or {}).get('configurable') or {}).get('thread_id')}"

    def resolve(
        sandbox: SandboxDefinition, config: ManagedRunConfig | None, *args: object
    ) -> StateBackend:
        fake = MagicMock(spec=StateBackend)
        fake.id = claim(sandbox, config)
        fake.aexecute = AsyncMock(return_value=ExecuteResponse(output="ok", exit_code=0))
        return fake

    monkeypatch.setattr(mda_runtime, "_claim_managed_sandbox", claim)
    monkeypatch.setattr(mda_runtime, "_resolve_managed_sandbox", resolve)
    # MDA writes each sandbox's proxy config before its commands, on the sandbox it named; a
    # test that looks at the writes records them (``proxy_writes``).
    peek = mda_runtime._peek_managed_host_name
    monkeypatch.setattr(
        mda_runtime,
        "_peek_managed_host_name",
        lambda key: peek(key) or f"sandbox-{key.removeprefix('thread:')}",
    )
    monkeypatch.setattr(
        mda_sandbox_proxy, "patch_sandbox_proxy_config", lambda sandbox, name, config: None
    )
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
    config = _thread(thread_id)
    config["configurable"] = {**config.get("configurable", {}), **context}
    await graph.ainvoke(
        {"messages": messages or [("user", "Review it")]},
        config,
        # The run context as the Agent Server hands it over, which LangGraph reads into the schema.
        context=context,  # ty: ignore[invalid-argument-type]
    )
    return graph


@pytest.mark.parametrize("graph", ["agent", "reviewer", "review-scout"])
async def test_every_run_and_read_shares_one_graph_and_calls_the_backend_as_itself(
    monkeypatch: pytest.MonkeyPatch, backend_calls: BackendCalls, graph: str
) -> None:
    """Runs share their graph and scope credentials; fresh replicas can read every graph."""
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
        store, "thread-1", _context(graph, "token-1", _catalog("web_search", "grant-1"))
    )
    second = await _run(
        store, "thread-2", _context(graph, "token-2", _catalog("list_findings", "grant-2"))
    )

    assert second.nodes["model"] is first.nodes["model"]
    assert [(bearer, operation) for _, bearer, operation in backend_calls if operation] == [
        ("Bearer token-1", "web_search:grant-1"),
        ("Bearer token-2", "list_findings:grant-2"),
    ]
    assert {bearer for _, bearer, _ in backend_calls} == {"Bearer token-1", "Bearer token-2"}

    # Another replica, which never built either graph, reads a thread's state.
    monkeypatch.setattr(mda_runtime._CachedDefinitions, "_built", OrderedDict())
    monkeypatch.setattr(mda_runtime._CachedGraphs, "_built", OrderedDict())
    read = await compile_managed_agent_async(
        agent.agent,
        _thread("thread-1"),
        runtime=_ReadRuntime(access_context="threads.read", store=store),
        report_telemetry=False,
        async_subagent_graphs=_WORKERS,
    )

    assert set(first.channels) <= set(read.channels)
    # Code mode's and the run's skills' state, which every graph has.
    assert {"_quickjs_snapshot_payload", "skills_metadata"} <= set(read.channels)


async def test_cached_graph_starts_each_native_root_with_ordinary_owner_metadata(
    monkeypatch: pytest.MonkeyPatch, backend_calls: BackendCalls
) -> None:
    from langsmith import Client, tracing_context

    monkeypatch.setattr(
        agent,
        "_DEFAULT_MODEL",
        GenericFakeChatModel(messages=iter([AIMessage(content="done")] * 3)),
    )
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGSMITH_API_KEY", "test")
    created: list[dict[str, object]] = []

    def capture(self: Client, **payload: object) -> None:
        extra = payload.get("extra")
        assert isinstance(extra, dict)
        created.append({**payload, "extra": {"metadata": dict(extra["metadata"])}})

    monkeypatch.setattr(Client, "create_run", capture)
    monkeypatch.setattr(Client, "update_run", lambda *args, **kwargs: None)
    store = InMemoryStore()
    graphs = []
    with tracing_context(enabled=True):
        for index, owner in enumerate(("alice", "bob", None)):
            catalog = _catalog("lookup", "grant").model_copy(
                update={"trace_metadata": {"origin": ("github", "dashboard", "schedule")[index]}}
            )
            context = _context("reviewer", f"owner-{index}", catalog)
            context["owner_ls_user_id"] = owner
            graphs.append(await _run(store, f"owner-{index}", context))
    assert graphs[0].nodes["model"] is graphs[1].nodes["model"]
    roots = [run for run in created if not run.get("parent_run_id")]
    assert len(roots) == 3
    owners = []
    origins = []
    for run in roots:
        extra = run["extra"]
        assert isinstance(extra, dict)
        owners.append(extra["metadata"].get("owner_ls_user_id"))
        origins.append(extra["metadata"].get("origin"))
    assert owners == ["alice", "bob", None]
    assert origins == ["github", "dashboard", "schedule"]


async def test_each_run_on_the_shared_graph_works_in_its_own_threads_sandbox(
    monkeypatch: pytest.MonkeyPatch, backend_calls: BackendCalls, prepared: Prepared
) -> None:
    """The graph is compiled once, yet each run asks for its own thread's sandbox before it
    prepares, and its shell reaches that sandbox, with the options its own catalog gives: the
    sandbox the backend recorded for its thread, as the existing sandbox MDA opens, and create
    fields the SDK does not model, none of which reaches the create request as an option. MDA
    creates the sandbox with the proxy config the catalog names and writes it there itself;
    that config never goes as a create field."""
    opened: set[tuple[object, object]] = set()
    resolved: dict[object, SandboxDefinition] = {}

    def claim(sandbox: SandboxDefinition, config: ManagedRunConfig | None, *args: object) -> str:
        thread_id = ((config or {}).get("configurable") or {}).get("thread_id")
        opened.add((thread_id, sandbox.options.get("snapshot_id")))
        resolved[thread_id] = sandbox
        return f"sandbox-{thread_id}"

    def resolve(
        sandbox: SandboxDefinition, config: ManagedRunConfig | None, *args: object
    ) -> StateBackend:
        fake = MagicMock(spec=StateBackend)
        fake.id = claim(sandbox, config)
        fake.aexecute = AsyncMock(return_value=ExecuteResponse(output="ok", exit_code=0))
        return fake

    monkeypatch.setattr(mda_runtime, "_claim_managed_sandbox", claim)
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
    catalog = _catalog("web_search", "g2", snapshot="b")
    assert catalog.sandbox is not None
    catalog.sandbox.sandbox_id = "recorded-box"
    catalog.sandbox.create.extra_fields = {
        "preserve_memory_on_stop": True,
        "proxy_config": {"rules": []},
        "run_config": {"user": "agent", "env_vars": {"LANG": "C.UTF-8"}},
    }
    catalog.sandbox.create.env_vars = {"GH_TOKEN": "proxy-injected"}
    second = await _run(store, "thread-2", _context("agent", "token-2", catalog))

    assert second.nodes["model"] is first.nodes["model"]
    assert prepared == [("sandbox-thread-1", None), ("sandbox-thread-2", None)]
    assert opened == {("thread-1", "a"), ("thread-2", "b")}
    options = resolved["thread-2"].options
    assert options["existing_sandbox"] == "recorded-box"
    assert "existing_sandbox" not in resolved["thread-1"].options
    assert options["_openswe_create_fields"] == {"preserve_memory_on_stop": True}
    assert options["default_timeout"] == EXECUTE_TIMEOUT_SECONDS
    create = mda_runtime._sandbox_create_kwargs(resolved["thread-2"])
    assert not [key for key in create if key.startswith("_openswe_") or key == "existing_sandbox"]
    assert create["proxy_config"] == _PROXY_CONFIG
    assert create["run_config"] == {
        "user": "agent",
        "env_vars": {"LANG": "C.UTF-8", "GH_TOKEN": "proxy-injected"},
    }


async def test_a_run_retries_a_sandbox_the_sandbox_service_failed_to_open_and_reports_why_it_gave_up(
    monkeypatch: pytest.MonkeyPatch, backend_calls: BackendCalls, prepared: Prepared
) -> None:
    """A 503 while opening or creating the sandbox is retried until it opens; a refusal that will not
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

    def claim(sandbox: SandboxDefinition, config: ManagedRunConfig | None, *args: object) -> str:
        thread_id = ((config or {}).get("configurable") or {}).get("thread_id")
        attempts.append(thread_id)
        if failures[thread_id]:
            raise failures[thread_id].pop(0)
        return f"sandbox-{thread_id}"

    def booted(
        sandbox: SandboxDefinition, config: ManagedRunConfig | None, *args: object
    ) -> StateBackend:
        thread_id = ((config or {}).get("configurable") or {}).get("thread_id")
        backend = MagicMock(spec=StateBackend)
        backend.id = f"sandbox-{thread_id}"
        backend.aexecute = AsyncMock(return_value=ExecuteResponse(output="", exit_code=0))
        return backend

    monkeypatch.setattr(mda_runtime, "_claim_managed_sandbox", claim)
    monkeypatch.setattr(mda_runtime, "_resolve_managed_sandbox", booted)
    monkeypatch.setattr("rt.managed_sandbox.asyncio.sleep", AsyncMock())
    answers = iter([AIMessage(content="done"), AIMessage(content="done")])
    monkeypatch.setattr(agent, "_DEFAULT_MODEL", GenericFakeChatModel(messages=answers))
    store = InMemoryStore()

    for thread_id, token in (("thread-1", "token-1"), ("thread-2", "token-2")):
        catalog = _catalog("web_search", token, snapshot="a")
        await _run(store, thread_id, _context("agent", token, catalog))

    assert attempts == ["thread-1", "thread-1", "thread-1", "thread-2"]
    assert prepared == [
        ("sandbox-thread-1", None),
        (None, "SandboxAuthenticationError: Invalid api_key=[redacted]"),
    ]


async def test_a_sandbox_that_never_boots_is_reported_to_preparing_and_ends_the_run(
    monkeypatch: pytest.MonkeyPatch, backend_calls: BackendCalls, prepared: Prepared
) -> None:
    """A run that prepares in its sandbox boots it first, to write its proxy config there: a
    sandbox that never boots is reported to preparing, so the backend can tell the person why,
    and the run ends at its first tool call."""
    reason = "ResourceCreationError: Image pull failed"
    monkeypatch.setattr("rt.managed_sandbox.asyncio.sleep", AsyncMock())
    # Where the sandbox's checkouts are, so the turn's git baseline is taken.
    monkeypatch.setitem(_PREPARE_UPDATE, "work_dir", "/workspace")
    monkeypatch.setattr(mda_runtime, "_claim_managed_sandbox", lambda *args: "sandbox-thread-1")
    monkeypatch.setattr(
        mda_runtime,
        "_resolve_managed_sandbox",
        MagicMock(side_effect=ResourceCreationError("Image pull failed", resource_type="sandbox")),
    )
    monkeypatch.setattr(
        agent, "_DEFAULT_MODEL", _calling(ToolCall(name="execute", args={"command": "ls"}, id="c"))
    )

    with pytest.raises(ResourceCreationError):
        await _run(
            InMemoryStore(),
            "thread-1",
            _context("agent", "token-1", _catalog("web_search", "g1", snapshot="a")),
        )

    assert prepared == [(None, reason)]


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
    elif failure in {"unusable", "conflict"}:
        # The unusable sandbox still holds the name; a concurrent create's gone again.
        if failure == "conflict":
            client.get_sandbox.side_effect = ResourceNotFoundError("not found")
        client.create_sandbox.side_effect = ResourceNameConflictError("name already exists")
    monkeypatch.setattr(mda_runtime, "_sandbox_client_for", lambda sandbox: client)
    spec = _catalog("lookup", "grant", snapshot="snapshot").sandbox
    assert spec is not None

    with pytest.raises(RuntimeError):
        mda_runtime._create_langsmith_sandbox(
            definition(spec.create), config=_thread("thread-1"), scope_key="thread:thread-1"
        )

    # At most one create, under the thread's own name, which another sandbox holds.
    names = {call.kwargs["name"] for call in client.create_sandbox.call_args_list}
    assert names <= {mda_runtime._managed_sandbox_name("open-swe-mda-prod", "thread:thread-1")}
    assert len(client.create_sandbox.call_args_list) == (
        0 if failure in {"lookup", "restart"} else 1
    )


def test_browser_finds_the_opened_per_thread_sandbox(monkeypatch: pytest.MonkeyPatch) -> None:
    spec = _catalog("lookup", "grant", snapshot="snapshot").sandbox
    assert spec is not None
    source = MagicMock()
    source.sandbox = definition(spec.create)
    source.run_config = None
    source.selection = mda_runtime._SandboxSelection("graph-selection", None)
    source.resolve = None
    source.declared = mda_runtime._RunSandbox(source.sandbox, source.selection)
    backend = mda_runtime._LazyManagedSandboxBackend(source, {"thread_id": "thread-1"})
    opened = MagicMock(spec=SandboxBackendProtocol)
    opened.id = "existing-sandbox"
    entry = mda_runtime._ManagedSandboxEntry(opened, host_name="existing-sandbox")
    monkeypatch.setattr(mda_runtime, "_managed_sandboxes", {"thread:thread-1": entry})
    runtime = MagicMock()
    runtime.backend = backend

    assert mda_browser._thread_sandbox(runtime) is opened


# The backend's LangSmith key, which starts the runs of threads it owns.
_BACKEND_CALLER: Final = {"identity": "backend", "permissions": [], "mda_user_id": "ls-backend"}


def _channel_caller(user_id: str, conversation: str) -> dict[str, object]:
    """A caller through MDA's channel ingress, for the Slack conversation that owns its threads."""
    return {
        "identity": user_id,
        "permissions": [],
        "mda_user_id": user_id,
        "mda_source_provider": "slack",
        "mda_source_thread_id": conversation,
    }


def _auth(caller: object, action: Literal["create", "read"]) -> AuthContext:
    return AuthContext(
        permissions=[], user=cast(BaseUser, caller), resource="threads", action=action
    )


async def _created_by(caller: object, thread_id: str) -> dict[str, object]:
    """A thread as MDA records it for the caller that created it."""
    created: dict[str, object] = {"thread_id": thread_id, "metadata": {}}
    await mda_identity_auth._scope_threads(_auth(caller, "create"), cast(ThreadsCreate, created))
    return created


@pytest.mark.parametrize("origin", ["dashboard", "slack"])
@pytest.mark.parametrize("run", ["launched", "follow-up"])
async def test_a_task_worker_works_in_its_coordinators_sandbox(
    monkeypatch: pytest.MonkeyPatch, origin: str, run: str
) -> None:
    """A task worker is MDA's async subagent of its coordinator, so MDA gives each of its runs
    the coordinator's sandbox: the run its coordinator launched, and the later ones the backend
    starts. A Slack coordinator's thread belongs to its conversation, not to a person, and so
    does the worker thread its run launched, which the backend's later runs act in as that
    conversation."""
    coordinator_caller = (
        _BACKEND_CALLER
        if origin == "dashboard"
        else _channel_caller("principal-alice", "T1:C1:1700000000.000100")
    )
    threads = {
        "coordinator": await _created_by(coordinator_caller, "coordinator"),
        # Launched in process during the coordinator's run, so as its caller.
        "worker": await _created_by(coordinator_caller, "worker"),
    }
    caller: object = coordinator_caller
    if run == "follow-up" and origin == "slack":
        worker_owner = cast(dict[str, str], threads["worker"]["metadata"])["owner"]
        provider, _, conversation = worker_owner.removeprefix("mda:source-thread:").partition(":")
        assert provider == "slack"
        caller = _channel_caller("mda:channel", conversation)
    elif run == "follow-up":
        caller = _BACKEND_CALLER

    async def read(thread_id: str) -> dict[str, object]:
        scope = await mda_identity_auth._scope_threads(
            _auth(caller, "read"), cast(ThreadsRead, {"thread_id": thread_id})
        )
        thread = threads[thread_id]
        metadata = cast(dict[str, object], thread["metadata"])
        if any(metadata.get(key) != value for key, value in (scope or {}).items()):
            raise LookupError(thread_id)
        return thread

    client = MagicMock()
    client.threads.get = AsyncMock(side_effect=read)
    monkeypatch.setattr(mda_runtime, "get_client", lambda: client)
    coordinators = MagicMock(spec=SandboxBackendProtocol)
    coordinators.id = "coordinator-sandbox"
    spec = _catalog("lookup", "grant", snapshot="snapshot").sandbox
    assert spec is not None
    # The worker's catalog names the sandbox the coordinator is bound to, as the
    # coordinator's own runs do once the backend has recorded it.
    worker_sandbox = definition(spec.create, "box")
    selection = mda_runtime._sandbox_selection(worker_sandbox)
    coordinator_scope = mda_runtime._thread_scope_key(_thread("coordinator"), selection)
    monkeypatch.setattr(
        mda_runtime,
        "_managed_sandboxes",
        {coordinator_scope: mda_runtime._ManagedSandboxEntry(coordinators, host_name="box")},
    )
    worker_run: ManagedRunConfig = {
        "configurable": {"thread_id": "worker", "langgraph_auth_user": caller},
        # The Agent Server copies the thread's metadata, deepagents' parent among it, into each run.
        "metadata": {"parent_thread_id": "coordinator", "subagent_name": "general-purpose"},
    }
    source = MagicMock()
    source.sandbox = worker_sandbox
    source.run_config = worker_run
    source.selection = selection
    source.resolve = None
    source.declared = mda_runtime._RunSandbox(source.sandbox, selection)

    async with mda_runtime.parent_run(worker_run):
        assert mda_runtime._thread_scope_key(worker_run, selection) == coordinator_scope
        backend = mda_runtime._LazyManagedSandboxBackend(source, None)
        assert mda_browser._thread_sandbox(MagicMock(backend=backend)) is coordinators


@pytest.mark.parametrize("replaced", [False, True])
def test_losing_a_thread_sandbox_only_reconnects_to_an_existing_replacement(
    monkeypatch: pytest.MonkeyPatch,
    sandbox_bindings: dict[str, dict[str, str | None]],
    replaced: bool,
) -> None:
    monkeypatch.setenv("MDA_DEPLOYMENT_NAME", "open-swe-mda-prod")
    monkeypatch.delenv("LANGSMITH_DEPLOYMENT_NAME", raising=False)
    spec = _catalog("lookup", "grant", snapshot="snapshot").sandbox
    assert spec is not None
    sandbox = definition(spec.create, proxy_config=spec.proxy_config)
    monkeypatch.setattr(mda_sandbox_proxy, "patch_sandbox_proxy_config", lambda *args: None)
    name = mda_runtime._managed_sandbox_name("open-swe-mda-prod", "thread:thread-1")
    original = MagicMock(spec=SandboxBackendProtocol)
    original.execute.side_effect = ResourceNotFoundError("sandbox is gone")
    entry = mda_runtime._ManagedSandboxEntry(original, host_name=name)
    cache = {"thread:thread-1": entry}
    monkeypatch.setattr(mda_runtime, "_managed_sandboxes", cache)
    # Another replica replaced the sandbox under its name, and bound the thread to the new one.
    sandbox_bindings["thread-1"] = {name: "new-id" if replaced else "old-id"}
    client = MagicMock()
    replacement = MagicMock(status="running")
    replacement.name = name
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

    # The gone sandbox is forgotten, so the next use finds what the thread is bound to.
    assert "thread:thread-1" not in cache
    if replaced:
        assert backend._backend(backend._source.declared) is resolved
    else:
        with pytest.raises(SandboxMissingError):
            backend._backend(backend._source.declared)
    client.create_sandbox.assert_not_called()


@pytest.mark.parametrize("missing", [False, True])
def test_persisted_sandbox_binding_overrides_another_replicas_canonical_cache(
    monkeypatch: pytest.MonkeyPatch, missing: bool
) -> None:
    spec = _catalog("lookup", "grant", snapshot="snapshot").sandbox
    assert spec is not None
    bound_name = "original-legacy-suffix"
    sandbox = definition(spec.create, bound_name)
    # As MDA selects the sandbox a run's resolve returns.
    selection = mda_runtime._sandbox_selection(sandbox)
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
        with pytest.raises(SandboxMissingError, match="no longer exists"):
            mda_runtime._resolve_managed_sandbox(sandbox, _thread("thread-1"), selection=selection)
    else:
        resolved_backend = mda_runtime._resolve_managed_sandbox(
            sandbox, _thread("thread-1"), selection=selection
        )
        assert resolved_backend is resolved
    assert all(call.args == (bound_name,) for call in client.get_sandbox.call_args_list)
    client.create_sandbox.assert_not_called()
    client.update_sandbox.assert_not_called()
    client.stop_sandbox.assert_not_called()
    client.delete_sandbox.assert_not_called()


async def test_a_deleted_bound_sandbox_is_reported_missing_and_never_recreated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A thread and its sandbox are one: the run reports the bound sandbox gone rather than
    working on in an empty one made under its name."""
    monkeypatch.setenv("MDA_DEPLOYMENT_NAME", "open-swe-mda-prod")
    spec = _catalog("lookup", "grant", snapshot="snapshot").sandbox
    assert spec is not None
    sandbox = definition(spec.create, "original-legacy-suffix")
    monkeypatch.setattr(mda_runtime, "_managed_sandboxes", {})
    client = MagicMock()
    client.get_sandbox.side_effect = ResourceNotFoundError("deleted")
    monkeypatch.setattr(mda_runtime, "_sandbox_client_for", lambda sandbox: client)
    backend = mda_runtime._managed_sandbox_backend(sandbox, _thread("thread-1"))

    opened = await opened_sandbox(MagicMock(backend=backend))

    assert opened.sandbox_id is None
    assert opened.error is not None and opened.error.startswith("SandboxMissingError")
    client.create_sandbox.assert_not_called()
    client.update_sandbox.assert_not_called()
    client.stop_sandbox.assert_not_called()
    client.delete_sandbox.assert_not_called()


@pytest.fixture
def sandbox_commands() -> list[str]:
    """Each command that reached a sandbox."""
    return []


@pytest.fixture
def proxy_writes(
    monkeypatch: pytest.MonkeyPatch, backend_calls: BackendCalls, sandbox_commands: list[str]
) -> list[tuple[str, object]]:
    """Each proxy config MDA writes, with the name of the sandbox it writes it on."""
    written: list[tuple[str, object]] = []

    def resolve(
        sandbox: SandboxDefinition, config: ManagedRunConfig | None, *args: object
    ) -> StateBackend:
        thread_id = ((config or {}).get("configurable") or {}).get("thread_id")

        async def execute(command: str, **kwargs: object) -> ExecuteResponse:
            sandbox_commands.append(command)
            return ExecuteResponse(output="ok", exit_code=0)

        fake = MagicMock(spec=StateBackend)
        fake.id = f"sandbox-{thread_id}"
        fake.aexecute = AsyncMock(side_effect=execute)
        # Where MDA's cache keeps the sandbox, which its proxy config write names.
        mda_runtime._managed_sandboxes[f"thread:{thread_id}"] = mda_runtime._ManagedSandboxEntry(
            fake, host_name=fake.id
        )
        return fake

    def patch(sandbox: SandboxDefinition, name: str, config: object) -> None:
        backend_calls.append(("proxy_config", name, None))
        written.append((name, config))

    monkeypatch.setenv("MDA_LOCAL_DEV", "1")
    monkeypatch.setattr(mda_runtime, "_managed_sandboxes", {})
    monkeypatch.setattr(mda_runtime, "_resolve_managed_sandbox", resolve)
    monkeypatch.setattr(
        mda_runtime,
        "_claim_managed_sandbox",
        lambda sandbox, config, *args: (
            f"sandbox-{((config or {}).get('configurable') or {}).get('thread_id')}"
        ),
    )
    monkeypatch.setattr(mda_sandbox_proxy, "patch_sandbox_proxy_config", patch)
    monkeypatch.setattr(
        agent, "_DEFAULT_MODEL", _calling(ToolCall(name="execute", args={"command": "ls"}, id="1"))
    )
    return written


async def test_mda_writes_the_whole_proxy_config_before_a_preparation_in_the_sandbox(
    backend_calls: BackendCalls,
    proxy_writes: list[tuple[str, object]],
) -> None:
    """Where the run prepares in its sandbox, as a review fetching its pull request does, MDA
    writes the proxy config the catalog names before the run prepares: preparing runs the
    backend's commands there, which MDA writes nothing before."""
    await _run(
        InMemoryStore(),
        "thread-1",
        _context("agent", "token-1", _catalog("web_search", "g1", snapshot="a")),
    )

    hooks = [hook for hook, _, _ in backend_calls]
    assert hooks.index("proxy_config") < hooks.index("prepare")
    assert proxy_writes == [("sandbox-thread-1", _PROXY_CONFIG)]


async def test_a_runs_first_model_call_goes_ahead_while_its_sandbox_boots(
    monkeypatch: pytest.MonkeyPatch,
    backend_calls: BackendCalls,
    proxy_writes: list[tuple[str, object]],
    sandbox_commands: list[str],
    prepared: Prepared,
    bound: Bound,
) -> None:
    """Where the run prepares without its sandbox, the sandbox boots behind the run's first
    model call, and MDA writes the proxy config there before the first command it runs."""
    answering = threading.Event()
    booted_behind_the_model: list[bool] = []
    resolve = mda_runtime._resolve_managed_sandbox

    def boot(
        sandbox: SandboxDefinition, config: ManagedRunConfig | None, *args: object
    ) -> SandboxBackendProtocol:
        # A sandbox that boots only once the model is answering.
        booted_behind_the_model.append(answering.wait(timeout=10))
        return resolve(sandbox, config)

    def replies() -> Iterator[AIMessage]:
        answering.set()
        yield AIMessage(content="", tool_calls=[ToolCall(name="web_search", args={}, id="1")])
        yield AIMessage(
            content="", tool_calls=[ToolCall(name="execute", args={"command": "ls"}, id="2")]
        )
        yield AIMessage(content="done")

    monkeypatch.setattr(mda_runtime, "_resolve_managed_sandbox", boot)
    monkeypatch.setattr(agent, "_DEFAULT_MODEL", GenericFakeChatModel(messages=replies()))
    catalog = _catalog("web_search", "g1", snapshot="a", bind_after_prepare=True)

    await _run(InMemoryStore(), "thread-1", _context("agent", "token-1", catalog))

    assert booted_behind_the_model and all(booted_behind_the_model)
    hooks = [hook for hook, _, _ in backend_calls]
    assert hooks.index("prepare") < hooks.index("proxy_config")
    assert proxy_writes == [("sandbox-thread-1", _PROXY_CONFIG)]
    assert "ls" in sandbox_commands


@pytest.mark.parametrize("external", [False, True])
async def test_a_run_answers_its_first_model_call_while_its_sandbox_is_opened_and_bound(
    monkeypatch: pytest.MonkeyPatch,
    backend_calls: BackendCalls,
    proxy_writes: list[tuple[str, object]],
    sandbox_commands: list[str],
    prepared: Prepared,
    bound: Bound,
    external: bool,
) -> None:
    """Where the catalog lets a run report its sandbox behind its preparation, the run prepares
    and its model answers before the sandbox is even open; its first tool call, one the
    backend runs included, waits for the sandbox to be opened and bound to the thread. MDA
    writes the proxy config there before its first command, but on an external sandbox, which
    keeps the one its harness gave it."""
    answering = threading.Event()
    opened_behind_the_model: list[bool] = []

    def claim(sandbox: SandboxDefinition, config: ManagedRunConfig | None, *args: object) -> str:
        # A sandbox that opens only once the model is answering.
        opened_behind_the_model.append(answering.wait(timeout=10))
        return "sandbox-thread-1"

    def replies() -> Iterator[AIMessage]:
        answering.set()
        yield AIMessage(content="", tool_calls=[ToolCall(name="web_search", args={}, id="1")])
        yield AIMessage(
            content="", tool_calls=[ToolCall(name="execute", args={"command": "ls"}, id="2")]
        )
        yield AIMessage(content="done")

    monkeypatch.setattr(mda_runtime, "_claim_managed_sandbox", claim)
    monkeypatch.setattr(agent, "_DEFAULT_MODEL", GenericFakeChatModel(messages=replies()))
    catalog = _catalog(
        "web_search",
        "g1",
        snapshot="a",
        bind_after_prepare=True,
        external=external,
    )

    await _run(InMemoryStore(), "thread-1", _context("agent", "token-1", catalog))

    assert opened_behind_the_model and all(opened_behind_the_model)
    # Preparing named no sandbox; the backend heard of it once it was open.
    assert prepared == [(None, None)]
    assert bound == [("sandbox-thread-1", None)]
    hooks = [hook for hook, _, _ in backend_calls]
    order = ["prepare", "bind_sandbox", "operation"]
    assert [hooks.index(hook) for hook in order] == sorted(hooks.index(hook) for hook in order)
    assert [name for name, _ in proxy_writes] == ([] if external else ["sandbox-thread-1"])
    assert "ls" in sandbox_commands


@pytest.mark.parametrize("failure", ["open", "bind"])
async def test_a_sandbox_that_cannot_be_opened_or_bound_behind_the_model_ends_the_run_at_its_first_tool_call(
    monkeypatch: pytest.MonkeyPatch,
    backend_calls: BackendCalls,
    proxy_writes: list[tuple[str, object]],
    sandbox_commands: list[str],
    bound: Bound,
    failure: str,
) -> None:
    """A run that reports its sandbox behind its preparation learns only after its model
    answered that it has none: its first tool call, whichever it is, ends the run, the
    backend hears why so it can tell the person, and nothing reaches a sandbox."""
    unreachable: list[JsonValue] = []
    reason = "SandboxAuthenticationError: Invalid api_key=[redacted]"
    if failure == "open":
        monkeypatch.setattr(
            mda_runtime,
            "_claim_managed_sandbox",
            MagicMock(side_effect=SandboxAuthenticationError("Invalid api_key=lsv2-secret")),
        )
    # The thread is bound to no sandbox, or to another than the run opened.
    monkeypatch.setitem(_BIND_ANSWER, "unreachable", reason)
    notify = Outbox.notify

    def recorded(outbox: Outbox, endpoint: Endpoint, request: RunRequest) -> None:
        if endpoint is SANDBOX_UNREACHABLE:
            unreachable.append(request.model_dump(mode="json")["reason"])
        notify(outbox, endpoint, request)

    monkeypatch.setattr(Outbox, "notify", recorded)
    monkeypatch.setattr(
        agent, "_DEFAULT_MODEL", _calling(ToolCall(name="web_search", args={}, id="1"))
    )
    catalog = _catalog("web_search", "g1", snapshot="a", bind_after_prepare=True)

    with pytest.raises(SandboxUnavailableError, match="Invalid api_key"):
        await _run(InMemoryStore(), "thread-1", _context("agent", "token-1", catalog))

    assert bound == [(None, reason) if failure == "open" else ("sandbox-thread-1", None)]
    assert unreachable == [reason]
    hooks = [hook for hook, _, _ in backend_calls]
    assert "operation" not in hooks
    assert sandbox_commands == []


async def test_a_run_that_calls_no_tool_ends_as_usual_whatever_became_of_its_sandbox(
    monkeypatch: pytest.MonkeyPatch,
    backend_calls: BackendCalls,
    proxy_writes: list[tuple[str, object]],
    bound: Bound,
) -> None:
    """A run that reports its sandbox behind its preparation and only answers never waits
    for the sandbox to answer, and ends normally though the sandbox could not be opened;
    the backend still hears of it before the run ends, so the thread's binding is settled."""
    answering = threading.Event()
    failed_behind_the_model: list[bool] = []

    def claim(sandbox: SandboxDefinition, config: ManagedRunConfig | None, *args: object) -> str:
        failed_behind_the_model.append(answering.wait(timeout=10))
        raise SandboxAuthenticationError("Invalid api_key=lsv2-secret")

    def replies() -> Iterator[AIMessage]:
        answering.set()
        yield AIMessage(content="42")

    monkeypatch.setattr(mda_runtime, "_claim_managed_sandbox", claim)
    monkeypatch.setattr(agent, "_DEFAULT_MODEL", GenericFakeChatModel(messages=replies()))
    catalog = _catalog("web_search", "g1", snapshot="a", bind_after_prepare=True)

    await _run(InMemoryStore(), "thread-1", _context("agent", "token-1", catalog))

    assert failed_behind_the_model == [True]
    assert bound == [(None, "SandboxAuthenticationError: Invalid api_key=[redacted]")]
    assert SANDBOX_UNREACHABLE.name not in [hook for hook, _, _ in backend_calls]


def _proxy_write_failure(failure: str) -> Exception:
    """What MDA's proxy config write raises when the sandbox API fails it as ``failure``."""
    if failure == "refused":
        return RuntimeError("Could not update sandbox proxy credentials.")
    if failure == "unavailable":
        return mda_sandbox_proxy.ProxyConfigRefusedError(
            "Could not update sandbox proxy credentials (HTTP 503)."
        )
    try:
        raise httpx.ConnectError("connection refused")
    except httpx.ConnectError:
        # As MDA's write hides a transport failure, which may carry credentials.
        try:
            raise RuntimeError("Could not update sandbox proxy credentials.") from None
        except RuntimeError as hidden:
            return hidden


@pytest.mark.parametrize("failure", ["refused", "unavailable", "disconnected"])
async def test_a_run_whose_proxy_config_cannot_be_written_prepares_with_its_sandbox_unreachable(
    monkeypatch: pytest.MonkeyPatch,
    backend_calls: BackendCalls,
    proxy_writes: list[tuple[str, object]],
    prepared: Prepared,
    failure: str,
) -> None:
    """Where the run prepares in its sandbox, a proxy config MDA cannot write there leaves the
    sandbox without the workspace's egress rules for the backend's commands: preparing hears
    the sandbox is unreachable, and why. A write the sandbox service failed transiently is
    retried, as opening the sandbox is, and the run prepares in its sandbox."""
    written = mda_sandbox_proxy.patch_sandbox_proxy_config
    attempts: list[str] = []

    def failing(sandbox: SandboxDefinition, name: str, config: dict[str, JsonValue]) -> None:
        attempts.append(name)
        if failure == "refused" or len(attempts) == 1:
            raise _proxy_write_failure(failure)
        written(sandbox, name, config)

    monkeypatch.setattr(mda_sandbox_proxy, "patch_sandbox_proxy_config", failing)
    monkeypatch.setattr("rt.managed_sandbox.asyncio.sleep", AsyncMock())
    monkeypatch.setattr(
        agent, "_DEFAULT_MODEL", GenericFakeChatModel(messages=iter([AIMessage(content="done")]))
    )

    await _run(
        InMemoryStore(),
        "thread-1",
        _context("agent", "token-1", _catalog("web_search", "g1", snapshot="a")),
    )

    if failure == "refused":
        assert attempts == ["sandbox-thread-1"]
        assert prepared == [(None, "RuntimeError: Could not update sandbox proxy credentials.")]
        assert proxy_writes == []
    else:
        assert attempts == ["sandbox-thread-1", "sandbox-thread-1"]
        assert prepared == [("sandbox-thread-1", None)]
        assert proxy_writes == [("sandbox-thread-1", _PROXY_CONFIG)]


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
    assert not _live_interpreters(interpreter)
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


async def test_a_disabled_tool_is_offered_but_refused_without_calling_the_backend(
    monkeypatch: pytest.MonkeyPatch, backend_calls: BackendCalls
) -> None:
    offered: list[set[str]] = []
    seen: list[BaseMessage] = []

    def bind(
        model: GenericFakeChatModel, tools: list[BaseTool], **kwargs: object
    ) -> GenericFakeChatModel:
        offered.append({tool.name for tool in tools})
        return model

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

    monkeypatch.setattr(GenericFakeChatModel, "bind_tools", bind)
    monkeypatch.setattr(GenericFakeChatModel, "_agenerate", answering)
    monkeypatch.setattr(
        agent, "_DEFAULT_MODEL", _calling(ToolCall(name="lookup", args={}, id="disabled"))
    )
    catalog = _catalog("lookup", "grant").model_copy(update={"disabled": ["lookup"]})

    await _run(InMemoryStore(), "disabled", _context("agent", "token", catalog))

    assert offered and all("lookup" in names for names in offered)
    [refused] = [m for m in seen if isinstance(m, ToolMessage) and m.tool_call_id == "disabled"]
    assert refused.status == "error" and "not available" in refused.text
    assert not [hook for hook, _, _ in backend_calls if hook == "operation"]


async def test_runs_search_and_resolve_the_set_their_catalog_names_read_once(
    monkeypatch: pytest.MonkeyPatch, backend_calls: BackendCalls
) -> None:
    """Runs whose catalogs name one set read it once, search it here, and know here
    which names it has none for; only the grant of a tool they call is the backend's."""
    monkeypatch.setattr(search_tools, "_read", OrderedDict())
    seen: list[ToolMessage] = []
    generate = GenericFakeChatModel._agenerate

    async def answering(
        model: GenericFakeChatModel,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: object,
    ) -> ChatResult:
        seen.extend(m for m in messages if isinstance(m, ToolMessage) and m not in seen)
        return await generate(model, messages, stop, run_manager)

    monkeypatch.setattr(GenericFakeChatModel, "_agenerate", answering)
    code = "await tools.withheldLookup({}); await tools.notOne({})"
    turn = [
        AIMessage(
            content="",
            tool_calls=[ToolCall(name="search_tools", args={"query": "look up"}, id="search")],
        ),
        AIMessage(content="", tool_calls=[ToolCall(name="eval", args={"code": code}, id="eval")]),
        AIMessage(
            content="", tool_calls=[ToolCall(name="load_integration_tools", args={}, id="no")]
        ),
        AIMessage(content="done"),
    ]
    monkeypatch.setattr(agent, "_DEFAULT_MODEL", GenericFakeChatModel(messages=iter(turn * 2)))
    catalog = _catalog("lookup", "grant").model_copy(
        update={"search_tools": True, "tool_set": "set-1"}
    )

    await _run(InMemoryStore(), "searching-1", _context("agent", "token-1", catalog))
    await _run(InMemoryStore(), "searching-2", _context("agent", "token-2", catalog))

    found = [m for m in seen if m.tool_call_id == "search"]
    assert len(found) == 2
    assert all(json.loads(m.text)[0]["name"] == "withheld_lookup" for m in found)
    hooks = [hook for hook, _, _ in backend_calls]
    # Read once for both runs, and never searched on the backend.
    assert hooks.count("tool_set") == 1
    assert "search_tools" not in hooks
    # The backend gave each run its grant of the tool it called, and heard of no other.
    called = [(hook, detail) for hook, _, detail in backend_calls if detail]
    assert called == [
        ("resolve_tool", "withheldLookup"),
        ("operation", "withheld_lookup:found-grant"),
        ("resolve_tool", "withheldLookup"),
        ("operation", "withheld_lookup:found-grant"),
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


async def test_a_failed_code_run_leaves_its_code_tools_with_the_run(
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

    interpreter = next(
        item for item in agent._parts("agent").middleware if isinstance(item, RunCodeInterpreter)
    )
    assert any(operation == "lookup:grant" for _, _, operation in backend_calls)
    # The run never reached `after_agent`; the shared middleware still keeps none of its tools.
    assert not interpreter._ptc_tools_by_slot


async def test_code_tools_list_each_runs_schema_in_a_message_not_the_system_prompt(
    monkeypatch: pytest.MonkeyPatch, backend_calls: BackendCalls
) -> None:
    prompts: list[str] = []
    systems: list[str] = []
    rendered: list[list[str]] = []
    render = run_tools.render_ptc_prompt

    def render_listing(tools: Sequence[BaseTool], *, tool_name: str) -> str:
        rendered.append([tool.description for tool in tools])
        return render(tools, tool_name=tool_name)

    monkeypatch.setattr(run_tools, "render_ptc_prompt", render_listing)
    original = GenericFakeChatModel._agenerate

    async def generate(
        model: GenericFakeChatModel, messages: list[BaseMessage], **kwargs: object
    ) -> ChatResult:
        systems.append(
            "\n".join(message.text for message in messages if isinstance(message, SystemMessage))
        )
        prompts.append(
            next(
                message.text
                for message in reversed(messages)
                if isinstance(message, HumanMessage)
                and isinstance(message.id, str)
                and message.id.startswith("code-tools:")
            )
        )
        interpreter = next(
            middleware
            for middleware in agent._parts("agent").middleware
            if isinstance(middleware, RunCodeInterpreter)
        )
        state: REPLState = {"messages": cast(list[AnyMessage], messages)}
        assert await interpreter.abefore_model(state, Runtime()) is None
        state["_summarization_event"] = {"cutoff_index": len(messages)}
        restored = await interpreter.abefore_model(state, Runtime())
        assert restored is not None
        assert isinstance(restored["messages"], list)
        assert len(restored["messages"]) == 1
        message = restored["messages"][0]
        assert isinstance(message, HumanMessage)
        assert message.text == prompts[-1]
        state["messages"] = [*messages, message]
        assert await interpreter.abefore_model(state, Runtime()) is None
        if len(prompts) % 2:
            run = current_run()
            run.adopted = run.catalog.model_copy(
                update={
                    "operations": [
                        spec.model_copy(update={"description": f"Updated {spec.description}"})
                        for spec in run.catalog.operations
                    ]
                }
            )
        return await original(model, messages)

    monkeypatch.setattr(GenericFakeChatModel, "_agenerate", generate)
    monkeypatch.setattr(
        agent,
        "_DEFAULT_MODEL",
        _calling(
            *[
                ToolCall(name="eval", args={"code": "1 + 1"}, id=f"eval-{index}")
                for index in range(2)
            ]
        ),
    )
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

    assert len(prompts) == 4
    assert "Updated Uses first_parameter" in prompts[1]
    assert "Updated Uses second_parameter" in prompts[3]
    assert "first_parameter" in prompts[0] and "second_parameter" not in prompts[0]
    assert "second_parameter" in prompts[2] and "first_parameter" not in prompts[2]
    assert len(set(systems)) == 1
    assert rendered == [
        ["Uses first_parameter"],
        ["Updated Uses first_parameter"],
        ["Uses second_parameter"],
        ["Updated Uses second_parameter"],
    ]


async def test_code_only_search_and_files_are_hidden_but_callable(
    monkeypatch: pytest.MonkeyPatch, backend_calls: BackendCalls
) -> None:
    offered: list[set[str]] = []
    outputs: list[str] = []

    def bind(
        model: GenericFakeChatModel, tools: list[BaseTool], **kwargs: object
    ) -> GenericFakeChatModel:
        offered.append({tool.name for tool in tools})
        return model

    original = GenericFakeChatModel._agenerate

    async def generate(
        model: GenericFakeChatModel, messages: list[BaseMessage], **kwargs: object
    ) -> ChatResult:
        outputs.extend(message.text for message in messages if isinstance(message, ToolMessage))
        return await original(model, messages)

    monkeypatch.setattr(GenericFakeChatModel, "bind_tools", bind)
    monkeypatch.setattr(GenericFakeChatModel, "_agenerate", generate)
    monkeypatch.setattr(
        agent,
        "_DEFAULT_MODEL",
        _calling(
            ToolCall(
                name="eval",
                args={
                    "code": 'console.log(await tools.searchTools({query: "lookup"})); console.log(await tools.ls({path: "/"}));'
                },
                id="code",
            )
        ),
    )
    catalog = _catalog("lookup", "grant").model_copy(
        update={"code_only": ["lookup", "search_tools", "ls"], "search_tools": True}
    )
    await _run(InMemoryStore(), "forced", _context("agent", "forced", catalog))
    assert offered and all(not names & {"lookup", "search_tools", "ls"} for names in offered)
    assert all("eval" in names for names in offered)
    assert any(hook == "search_tools" for hook, _, _ in backend_calls)
    assert outputs and not any(
        "TypeError" in output or "ReferenceError" in output for output in outputs
    )


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


@pytest.mark.parametrize("graph", ["agent", "reviewer"])
async def test_each_runs_own_turn_calls_are_traced_as_the_root_agent(
    monkeypatch: pytest.MonkeyPatch, backend_calls: BackendCalls, graph: str
) -> None:
    """LangSmith's thread view shows the calls traced as the root agent as the thread's main
    conversation: the agent's own turn calls, not the summary deepagents compacts its
    history with."""

    async def generate(
        model: GenericFakeChatModel, messages: list[BaseMessage], **kwargs: object
    ) -> ChatResult:
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content="done"))])

    def build(spec: ModelSpec, **kwargs: object) -> GenericFakeChatModel:
        return GenericFakeChatModel(
            messages=iter([]), name=spec.model_id, profile={"max_input_tokens": 40_000}
        )

    monkeypatch.setattr(GenericFakeChatModel, "_agenerate", generate)
    monkeypatch.setattr(agent, "build_model", build)
    monkeypatch.setattr("rt.middleware.build_model", build)
    monkeypatch.setattr("rt.run_tools.build_model", build)
    monkeypatch.setattr(
        agent, "_DEFAULT_MODEL", build(ModelSpec(model_id="placeholder", kwargs={}))
    )
    created: list[dict[str, object]] = []
    monkeypatch.setattr(Client, "create_run", lambda self, **run: created.append(run))
    monkeypatch.setattr(Client, "update_run", lambda self, *args, **kwargs: None)
    model = ModelSpec(model_id="openai:gpt-test", kwargs={})
    catalog = _catalog("web_search", "grant").model_copy(
        update={"models": RunModels(model=model, subagent_model=model)}
    )
    history: list[tuple[str, str]] = [("user", "history " * 600) for _ in range(40)]

    with tracing_context(enabled=True):
        await _run(InMemoryStore(), "thread", _context(graph, "token-0", catalog), history)

    traced = set[tuple[str, object]]()
    for run in created:
        if run["run_type"] == "llm":
            metadata = cast(dict[str, dict[str, object]], run["extra"])["metadata"]
            kind = "summary" if metadata.get("lc_source") == "summarization" else "turn"
            traced.add((kind, metadata.get("ls_agent_type")))
    assert traced == {("summary", None), ("turn", "root")}


async def test_a_cached_agent_builds_once_per_inputs_and_never_sees_the_run(
    backend_calls: BackendCalls,
) -> None:
    """The first run on some inputs builds the graph every later run on them shares, so
    nothing of that run may reach the build: only the inputs do."""
    builds: list[tuple[str, bool]] = []

    class Inputs(BaseModel):
        graph: str

    def inputs(runtime: ManagedServerRuntime) -> Inputs:
        execution = runtime.execution_runtime
        context = execution.context if execution is not None else None
        return Inputs(graph=str((context or {}).get("graph")))

    @cached_agent(keyed_by=inputs)
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


async def test_only_app_requested_reviews_receive_browser_tools_and_instructions(
    monkeypatch: pytest.MonkeyPatch, backend_calls: BackendCalls, browsers: Browsers, tmp_path: Path
) -> None:
    """Only app-requested reviews get browser tools and instructions."""
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
    review_browser = BrowserSpec(session_domains=["review.example.com"])
    capturing = _catalog("lookup", "grant").model_copy(update={"browser": review_browser})
    await _run(store, "captured", _context("reviewer", "capturing", capturing))
    await _run(store, "browsed", _context("agent", "agent", _catalog("lookup", "grant")))
    await asyncio.sleep(0)

    tools = {"browser_run", "browser_snapshot", "browser_screenshot"}
    assert tools <= offered["capturing"] and "browser_handoff" not in offered["capturing"]
    for run in ("agent", "reviewer"):
        assert not {name for name in offered[run] if name.startswith("browser_")}
    assert instructed == {"agent": False, "capturing": True, "reviewer": False}
    captured = mda_browser._browser_name("thread:captured")
    assert sorted(browsers) == sorted([(captured, False), (captured, True)])


async def test_a_review_installs_sign_in_sessions_only_for_its_own_domains(
    monkeypatch: pytest.MonkeyPatch, backend_calls: BackendCalls, browsers: Browsers
) -> None:
    """A preview page can ask a review to sign in elsewhere; its browser refuses the session."""
    from rt.session_handoff import _HELD

    answers: list[str] = []

    async def generate(
        model: GenericFakeChatModel, messages: list[BaseMessage], **kwargs: object
    ) -> ChatResult:
        results = [message for message in messages if isinstance(message, ToolMessage)]
        if results:
            answers.extend(message.text for message in results)
            reply = AIMessage(content="done")
        else:
            # As an MCP result holds it: the model sees only the handle.
            _HELD.get(current_run())["production"] = {
                "host": "smith.langchain.com",
                "storage_key": "sb-auth-token",
                "session": {"access_token": "secret"},
            }
            call = ToolCall(name="browser_run", args={"session_handle": "production"}, id="in")
            reply = AIMessage(content="", tool_calls=[call])
        return ChatResult(generations=[ChatGeneration(message=reply)])

    async def no_browser(*args: object, **kwargs: object) -> mda_browser._Session:
        raise AssertionError("a refused session must not open the browser")

    monkeypatch.setattr(GenericFakeChatModel, "bind_tools", lambda self, *a, **k: self)
    monkeypatch.setattr(GenericFakeChatModel, "_agenerate", generate)
    monkeypatch.setattr(agent, "_DEFAULT_MODEL", _calling())
    monkeypatch.setattr(mda_browser, "_open", no_browser)
    review_browser = BrowserSpec(session_domains=["review.smith.langchain.com"])
    catalog = _catalog("lookup", "grant").model_copy(update={"browser": review_browser})

    await _run(InMemoryStore(), "signed", _context("reviewer", "signing", catalog))

    assert any("signs in only to its base and preview" in answer for answer in answers)


@pytest.mark.parametrize("empty_window", [False, True])
async def test_sign_in_uses_the_threads_tab_without_chrome_focus(empty_window: bool) -> None:
    from rt.browser import ReportedBrowserMiddleware

    page = MagicMock(goto=AsyncMock(), evaluate=AsyncMock())
    context = MagicMock(
        active_page=AsyncMock(return_value=None),
        pages=AsyncMock(return_value=[] if empty_window else [page]),
        new_page=AsyncMock(return_value=page),
    )
    session = MagicMock()
    session.stagehand.browser.context = context
    middleware = ReportedBrowserMiddleware(MagicMock(), graph="agent")

    result = await middleware._install(
        "beta.smith.langchain.com", "sb-test-auth-token", {"access_token": "test-token"}, session
    )

    assert result == "Session installed."
    assert page.goto.await_args_list[-1].args == ("https://beta.smith.langchain.com",)
    script = page.evaluate.await_args.args[0]
    assert 'location.origin !== "https://beta.smith.langchain.com"' in script
    assert 'localStorage.setItem("sb-test-auth-token"' in script
    assert "test-token" in script


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


async def test_a_tool_result_the_interpreter_cannot_take_fails_the_eval_not_the_run() -> None:
    nested: list[object] = []
    for _ in range(200):
        nested = [nested]

    @tool
    def nest() -> list[object]:
        """Return a result nested deeper than the interpreter marshals."""
        return nested

    slot = _Slot("nest", _SlotConfig())
    try:
        failed = await slot.eval_async("globalThis.kept = 1; await tools.nest({})", tools=[nest])
        assert failed.error_type == "MarshalError"
        assert (await slot.eval_async("kept")).result == "1"
    finally:
        await slot.aclose()


@pytest.mark.parametrize("explicit_tracer", [False, True])
@pytest.mark.parametrize("managed", [False, True])
@pytest.mark.parametrize("worker", [False, True])
async def test_a_distributed_parent_keeps_graph_nodes_under_the_graph_run(
    monkeypatch: pytest.MonkeyPatch, explicit_tracer: bool, managed: bool, worker: bool
) -> None:
    """A worker's run is also tagged ``subagent`` run-wide, which must not split its trace."""
    from langchain_core.runnables import RunnableConfig
    from managed_deepagents._run_input import ManagedCompiledAgent

    client = MagicMock(spec=Client)
    monkeypatch.setattr("langchain_core.tracers.langchain.get_client", lambda: client)
    monkeypatch.setattr("langsmith.run_trees.get_cached_client", lambda: client)
    parent = RunTree(name="dashboard_send", run_type="chain", ls_client=client)
    config: RunnableConfig = {
        "configurable": {
            "langsmith-trace": parent.dotted_order,
            "langsmith-metadata": {"ls_nest_under_parent": True},
            **({"deepagents_parent": {"thread_id": "lead"}} if worker else {}),
        },
    }
    if explicit_tracer:
        config["callbacks"] = [LangChainTracer(client=client, project_name="test")]

    class State(BaseModel):
        answer: str = ""

    async def step(state: State) -> dict[str, str]:
        return {"answer": state.answer + "done"}

    graph = StateGraph(State)
    graph.add_node("first", step)
    graph.add_node("second", step)
    graph.add_edge(START, "first")
    graph.add_edge("first", "second")
    graph.add_edge("second", END)
    compiled = graph.compile()
    if managed:
        compiled.__class__ = ManagedCompiledAgent
    with tracing_context(enabled=True), mda_runtime._parent_trace(config):
        assert await compiled.ainvoke(State(), config) == {"answer": "donedone"}

    # Every post counts: a tracer that never saw the graph run posts a node again as a root.
    created = [call.kwargs for call in client.create_run.call_args_list]
    runs = {run["id"]: run for run in created}
    [root] = [run for run in runs.values() if run["name"] == "LangGraph"]
    assert root["parent_run_id"] == parent.id
    assert {run["name"] for run in runs.values()} >= {"first", "second"}
    for run in created:
        assert run["trace_id"] == parent.trace_id
        ancestor = run
        while ancestor["id"] != root["id"]:
            ancestor = runs[ancestor["parent_run_id"]]
