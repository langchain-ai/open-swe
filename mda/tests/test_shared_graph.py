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
from deepagents.backends import StateBackend
from deepagents.backends.protocol import ExecuteResponse
from deepagents.middleware.summarization import DEEPAGENTS_DEFAULT_SUMMARY_PROMPT
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, BaseMessage, SystemMessage, ToolCall, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables.config import var_child_runnable_config
from langchain_core.tools import BaseTool
from langgraph.config import get_config
from langgraph.graph.state import CompiledStateGraph
from langgraph.store.memory import InMemoryStore
from langgraph_sdk.runtime import _ExecutionRuntime, _ReadRuntime
from langsmith.sandbox import (
    ResourceCreationError,
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
from runtime_api.reports import Reports

_WORKERS = {"general-purpose": "open-swe--general-purpose"}

type BackendCalls = list[tuple[str, str, str | None]]
type Prepared = list[tuple[JsonValue, JsonValue]]
type Browsers = list[tuple[JsonValue, JsonValue]]


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
