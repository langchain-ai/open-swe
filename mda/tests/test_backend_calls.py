import asyncio
import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from contextlib import closing
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Final, Literal, Self, cast
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import anthropic
import httpx
import httpx2
import openai
import pytest
from deepagents import create_deep_agent
from deepagents.backends.local_shell import LocalShellBackend
from langchain.agents import create_agent
from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langchain.tools import ToolRuntime
from langchain_anthropic import ChatAnthropic
from langchain_core.callbacks import AsyncCallbackManagerForLLMRun
from langchain_core.language_models.fake_chat_models import (
    FakeListChatModel,
    GenericFakeChatModel,
)
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    ToolCall,
    ToolMessage,
    messages_from_dict,
    messages_to_dict,
)
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import RunnableConfig, RunnableLambda
from langchain_core.tools import tool
from langchain_core.tracers.langchain import LangChainTracer, wait_for_all_tracers
from langchain_core.tracers.run_collector import RunCollectorCallbackHandler
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph_sdk.errors import (
    APIStatusError,
    InternalServerError,
    PermissionDeniedError,
    RateLimitError,
)
from langsmith import Client, agent_address, get_tracing_context, tracing_context
from langsmith.run_trees import RunTree, WriteReplica
from managed_deepagents import (
    DeepAgentDefinition,
    ManagedServerRuntime,
    define_deep_agent,
    shared_factory,
)
from managed_deepagents._run_input import ManagedTurnTracer
from managed_deepagents.runtime import compile_managed_agent_async
from pydantic import BaseModel, JsonValue
from rt.backend import (
    BackendCallError,
    BackendUnavailableError,
    OpenSweBackend,
    Run,
    RunContext,
    held_state,
)
from rt.file_tools import FileToolsMiddleware
from rt.managed_sandbox import box
from rt.middleware import BackendRunMiddleware, RunState, RuntimeToolsMiddleware
from rt.model_calls import model_call_guards
from rt.run_prompt import RunPromptMiddleware, ThreadPromptMiddleware
from rt.tools import operation_tool
from rt.tracing import TurnTrace
from runtime_api.catalog import (
    Catalog,
    FilesSpec,
    InboxSpec,
    ModelSpec,
    RunModels,
    SandboxCreate,
    SandboxSpec,
    ToolSpec,
)
from runtime_api.endpoint import Delivery, Endpoint, call
from runtime_api.loop import (
    AFTER_AGENT,
    AFTER_MODEL,
    BEFORE_MODEL,
    PREPARE,
    AfterAgent,
    BeforeModel,
    GitBaseline,
    Inbox,
    InboxItems,
    InboxRead,
    InboxReceipt,
    ModelCall,
    PhaseResult,
    Prepare,
    RunCall,
    ToolCallReport,
    ToolResultReply,
)
from runtime_api.operations import OperationResult, ShellVerdict
from runtime_api.reports import REPORTS, Reports, ReportsTaken
from runtime_api.workers import LAUNCH_WORKER


class UserInterrupt(Exception):
    pass


class UserTimeout(Exception):
    pass


@pytest.mark.parametrize(
    ("failure", "reason"),
    [
        (None, "completed"),
        (
            asyncio.CancelledError(
                UserInterrupt(
                    "superseded by run 00000000-0000-4000-8000-000000000001 "
                    "(multitask_strategy=interrupt)"
                )
            ),
            "superseded",
        ),
        (TimeoutError("run deadline exceeded"), "failed"),
        (asyncio.CancelledError(TimeoutError("run deadline exceeded")), "failed"),
        (asyncio.CancelledError(UserTimeout("run deadline exceeded")), "failed"),
        (asyncio.CancelledError(UserTimeout(TimeoutError("run deadline exceeded"))), "failed"),
        (asyncio.CancelledError(UserInterrupt("User interrupted the run")), "cancelled"),
        (asyncio.CancelledError(), "failed"),
        (GeneratorExit(), "cancelled"),
        (RuntimeError("provider failed"), "failed"),
    ],
)
async def test_managed_root_records_its_end_reason(
    failure: BaseException | None, reason: str
) -> None:
    client = MagicMock(spec=Client)
    tracer = ManagedTurnTracer(client=client, project_name="test")
    root_id = uuid4()
    with tracing_context(enabled=True):
        tracer.on_chain_start({}, {}, run_id=root_id)
        if failure is None:
            tracer.on_chain_end({"answer": "done"}, run_id=root_id)
        else:
            tracer.on_chain_error(failure, run_id=root_id)
    patches = [
        call.kwargs for call in client.update_run.call_args_list if call.kwargs["run_id"] == root_id
    ]
    assert patches
    assert _run_metadata(patches[-1])["end_reason"] == reason
    assert bool(patches[-1].get("error")) == (reason == "failed")
    if reason == "failed":
        assert failure is not None
        assert str(failure) in str(patches[-1]["error"])


async def test_managed_run_deadline_preserves_the_root_error() -> None:
    client = MagicMock(spec=Client)
    agent = await compile_managed_agent_async(
        define_deep_agent(
            name="timed-agent",
            model=_StalledAnthropic(model_name="claude-opus-5-5", api_key="test"),
        ),
        {},
        report_telemetry=False,
    )
    with tracing_context(enabled=True, client=client):
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(
                agent.ainvoke({"messages": [("user", "hello")]}, {"run_name": "timed-agent"}),
                timeout=0.1,
            )
    root_id = next(
        call.kwargs["id"]
        for call in client.create_run.call_args_list
        if call.kwargs["name"] == "timed-agent"
    )
    patches = [
        call.kwargs for call in client.update_run.call_args_list if call.kwargs["run_id"] == root_id
    ]
    assert patches
    assert _run_metadata(patches[-1])["end_reason"] == "failed"
    assert "CancelledError" in str(patches[-1]["error"])


async def test_root_termination_closes_children_and_suppresses_late_spans() -> None:
    client = MagicMock(spec=Client)
    tracer = ManagedTurnTracer(client=client, project_name="test")
    root_id, tool_id, model_id = uuid4(), uuid4(), uuid4()
    with tracing_context(enabled=True):
        root = tracer.on_chain_start({}, {}, run_id=root_id, name="turn")
        tracer.on_tool_start({}, "input", run_id=tool_id, parent_run_id=root_id)
        tracer.on_chat_model_start(
            {}, [[HumanMessage(content="hello")]], run_id=model_id, parent_run_id=root_id
        )
        tracer.on_chain_error(asyncio.CancelledError(), run_id=root_id)
        tracer.on_tool_end("late result", run_id=tool_id)
        tracer.on_llm_error(RuntimeError("late error"), run_id=model_id)
        posted = client.create_run.call_count
        late_id = uuid4()
        tracer.on_chain_start({}, {}, run_id=late_id, parent_run_id=tool_id)
        tracer.on_tool_start({}, "late", run_id=uuid4(), parent_run_id=late_id)
        tracer.on_chat_model_start(
            {}, [[HumanMessage(content="late")]], run_id=uuid4(), parent_run_id=root_id
        )
    assert client.create_run.call_count == posted
    ended = {
        call.kwargs["run_id"]: call.kwargs["end_time"] for call in client.update_run.call_args_list
    }
    assert ended[root_id] == ended[tool_id] == ended[model_id] == root.end_time


def _run_metadata(patch: Mapping[str, object]) -> Mapping[str, object]:
    """The metadata a LangSmith run update sets."""
    extra = patch.get("extra")
    metadata = extra.get("metadata") if isinstance(extra, Mapping) else None
    return metadata if isinstance(metadata, Mapping) else {}


type Answer = Callable[[str, dict[str, JsonValue]], Awaitable[BaseModel]]


# A run planned with nothing in particular.
_NO_PLAN: Final = Catalog()
# A run whose files are in MDA's box.
_IN_BOX: Final = SandboxSpec(
    create=SandboxCreate(fs_capacity_bytes=1, idle_ttl_seconds=60, delete_after_stop_seconds=60)
)


def _run_backend(
    token: str | None = "secret-token",
    *,
    worker_grant: str | None = None,
    invocation_id: str | None = None,
    catalog: Catalog = _NO_PLAN,
) -> OpenSweBackend:
    """The backend as a run of its own, outside any graph's run, started with ``catalog``."""
    context = RunContext.model_validate(
        {
            "__run_token": token,
            "__worker_grant": worker_grant,
            "invocation_id": invocation_id,
            "__catalog": catalog.model_dump(mode="json"),
        }
    )
    return Run(context).backend


def _answering(monkeypatch: pytest.MonkeyPatch, backend: OpenSweBackend, answer: Answer) -> None:
    """Have ``answer`` answer every call ``backend`` makes, by endpoint name."""

    async def post(
        path: str,
        body: dict[str, JsonValue],
        *,
        timeout: float,
        delivery: Delivery,
        headers: Mapping[str, str] | None = None,
    ) -> JsonValue:
        name = path.rsplit("/", 1)[-1]
        if name != REPORTS.name:
            return (await answer(name, body)).model_dump(mode="json")
        # Each item of a batch, answered as the call it stands for.
        replies = [
            (await answer(item.endpoint, item.request.model_dump(mode="json"))).model_dump(
                mode="json"
            )
            for item in Reports.model_validate(body).items
        ]
        return ReportsTaken(replies=replies).model_dump(mode="json")

    monkeypatch.setattr(backend, "post", post)


def _evidence(
    kind: Literal["work", "pr"], key: str, repo: str, branch: str, observed_at: int
) -> dict[str, JsonValue]:
    """Git evidence as the backend's hooks record it for the turn ``turn``."""
    return {
        f"git_trace_{kind}:{key}": {
            "invocation": "turn",
            "observed_at": observed_at,
            "targets": [{"repo": repo, "branch": branch}],
        }
    }


@pytest.mark.parametrize(
    ("replicate", "override", "projects"),
    [
        (False, "canonical", {"canonical"}),
        (True, "canonical", {"canonical", "deployment"}),
        (False, None, {"canonical", "deployment"}),
    ],
)
@pytest.mark.parametrize("has_parent", [False, True])
async def test_managed_traces_keep_the_requested_project_without_mirroring(
    monkeypatch: pytest.MonkeyPatch,
    replicate: bool,
    override: str | None,
    projects: set[str],
    has_parent: bool,
) -> None:
    monkeypatch.setattr(FakeListChatModel, "bind_tools", lambda self, *args, **kwargs: self)
    client = MagicMock()
    example_id = "00000000-0000-4000-8000-000000000001"
    replicas: list[WriteReplica] = [
        {
            "project_name": "canonical",
            "updates": {"reference_example_id": example_id},
            "auth": {"api_key": "synthetic-replica-key"},
        },
        {"project_name": "deployment"},
    ]
    parent = RunTree(name="upstream", ls_client=client, replicas=replicas)
    parent.post()
    parent_spans = {
        call.kwargs["session_name"]: call.kwargs["id"] for call in client.create_run.call_args_list
    }
    client.reset_mock()
    agent = await compile_managed_agent_async(
        define_deep_agent(
            name="test-agent",
            model=FakeListChatModel(responses=["first", "second"]),
            **({"replicate_traces": False} if not replicate else {}),
        ),
        {"configurable": {"__langsmith_project__": override}},
        report_telemetry=False,
    )
    callbacks = RunCollectorCallbackHandler()
    with tracing_context(
        enabled=True, client=client, parent=parent if has_parent else False, replicas=replicas
    ):
        for answer in ("first", "second"):
            result = await agent.ainvoke(
                {"messages": [("user", "hello")]},
                config={"run_name": "test-agent", "callbacks": [callbacks]},
            )
            assert result["messages"][-1].content == answer
            assert get_tracing_context()["replicas"] == replicas
    wait_for_all_tracers()
    assert len([run for run in callbacks.traced_runs if run.name == "test-agent"]) == 2
    root_ids = {
        call.kwargs["id"]
        for call in client.create_run.call_args_list
        if call.kwargs["name"] == "test-agent"
    }
    assert root_ids
    root_patches = {
        call.kwargs["run_id"]: _run_metadata(call.kwargs)
        for call in client.update_run.call_args_list
        if call.kwargs["run_id"] in root_ids
    }
    assert root_ids <= root_patches.keys()
    assert all(metadata["end_reason"] == "completed" for metadata in root_patches.values())
    spans = [call.kwargs for call in client.create_run.call_args_list]
    assert {span["session_name"] for span in spans} == projects
    for project in projects:
        project_spans = [span for span in spans if span["session_name"] == project]
        roots = [span for span in project_spans if span["name"] == "test-agent"]
        models = [span for span in project_spans if span["run_type"] == "llm"]
        assert len(roots) == len(models) == 2
        assert len({model["id"] for model in models}) == 2
        for root in roots:
            assert root["parent_run_id"] == (parent_spans[project] if has_parent else None)
            [model] = [m for m in models if str(root["id"]) in m["dotted_order"]]
            assert model["trace_id"] == root["trace_id"]
            if project == "canonical":
                assert root["reference_example_id"] == example_id
                assert root["_replica_auths"][0].api_key == "synthetic-replica-key"
    assert parent.replicas == replicas


@pytest.mark.parametrize(
    ("failure", "failures", "attempts"),
    [("connect", 10, 11), ("connect", 11, 11), (503, 1, 1), (504, 1, 1)],
)
async def test_a_tool_call_the_backend_never_answered(
    monkeypatch: pytest.MonkeyPatch, failure: Literal["connect"] | int, failures: int, attempts: int
) -> None:
    monkeypatch.setenv("OPEN_SWE_BACKEND_URL", "https://backend.invalid")
    monkeypatch.setattr("rt.tools.run_id", lambda: "run-1")
    monkeypatch.setattr("rt.backend.asyncio.sleep", AsyncMock())
    request = httpx2.Request("POST", "https://backend.invalid")
    response = httpx2.Response(
        200, request=request, json=OperationResult(content="done").model_dump(mode="json")
    )
    failure_result = (
        httpx2.ConnectError("secret-token")
        if failure == "connect"
        else httpx2.Response(failure, request=request)
    )
    client = MagicMock(is_closed=False)
    client.post = AsyncMock(side_effect=[failure_result] * failures + [response])
    monkeypatch.setattr("rt.backend.httpx2.AsyncClient", lambda **kwargs: client)
    served = operation_tool(
        ToolSpec(name="background_execute", description="Execute", parameters={"type": "object"}),
        _run_backend(),
    )
    runtime = ToolRuntime(
        state={"messages": []},
        context=None,
        config={},
        stream_writer=lambda event: None,
        tool_call_id="call-1",
        store=None,
    )
    assert served.coroutine is not None
    result_message = await served.coroutine(runtime=runtime)
    assert isinstance(result_message, ToolMessage)
    assert result_message.tool_call_id == "call-1"
    assert result_message.status == ("success" if failures == 10 else "error")
    assert "secret-token" not in result_message.content
    assert client.post.await_count == attempts


@pytest.mark.parametrize(
    ("arguments", "takes", "succeeds"),
    [({}, 121.0, False), ({"timeout": 1200}, 1500.0, True)],
)
async def test_a_quick_operation_deadline_yields_to_a_requested_timeout(
    monkeypatch: pytest.MonkeyPatch,
    arguments: dict[str, JsonValue],
    takes: float,
    succeeds: bool,
) -> None:
    monkeypatch.setenv("OPEN_SWE_BACKEND_URL", "https://backend.invalid")
    monkeypatch.setattr("rt.tools.run_id", lambda: "run-1")
    request = httpx2.Request("POST", "https://backend.invalid")

    async def post(*args: object, timeout: float, **kwargs: object) -> httpx2.Response:
        if takes > timeout:
            raise httpx2.ReadTimeout("stalled backend")
        return httpx2.Response(200, request=request, json={"content": "done"})

    client = MagicMock(is_closed=False)
    client.post = post
    monkeypatch.setattr("rt.backend.httpx2.AsyncClient", lambda **kwargs: client)
    served = operation_tool(
        ToolSpec(
            name="get_thread",
            description="Operation",
            parameters={"type": "object"},
            seconds=120.0,
        ),
        _run_backend(),
    )
    runtime = ToolRuntime(
        state={"messages": []},
        context=None,
        config={},
        stream_writer=lambda event: None,
        tool_call_id="call-1",
        store=None,
    )
    assert served.coroutine is not None
    result = await served.coroutine(runtime=runtime, **arguments)
    assert isinstance(result, ToolMessage)
    if succeeds:
        assert result.content == "done"
    else:
        assert result.status == "error"
        assert "did not answer" in result.content


@pytest.mark.parametrize(
    ("endpoint", "status"),
    [
        (PREPARE, 504),
        (BEFORE_MODEL, 502),
        (BEFORE_MODEL, 504),
        (AFTER_MODEL, 502),
        (AFTER_MODEL, 504),
        (AFTER_AGENT, 504),
    ],
)
async def test_phase_gateway_failure_is_not_replayed(
    monkeypatch: pytest.MonkeyPatch, endpoint: Endpoint, status: int
) -> None:
    monkeypatch.setenv("OPEN_SWE_BACKEND_URL", "https://backend.invalid")
    sleep = AsyncMock()
    monkeypatch.setattr("rt.backend.asyncio.sleep", sleep)
    request = httpx2.Request("POST", "https://backend.invalid")
    client = MagicMock(is_closed=False)
    client.post = AsyncMock(
        side_effect=[httpx2.Response(status, request=request)]
        + [httpx2.Response(200, request=request, json={"update": {"recovered": True}})]
    )
    monkeypatch.setattr("rt.backend.httpx2.AsyncClient", lambda **kwargs: client)

    with pytest.raises(BackendUnavailableError):
        await call(_run_backend(), endpoint, endpoint.request())

    assert client.post.await_count == 1
    sleep.assert_not_awaited()


@pytest.mark.parametrize(("failures", "recovers"), [(10, True), (11, False)])
@pytest.mark.parametrize("endpoint", [PREPARE, LAUNCH_WORKER, BEFORE_MODEL, AFTER_MODEL])
async def test_a_run_waits_out_a_backend_rollout(
    monkeypatch: pytest.MonkeyPatch, endpoint: Endpoint, failures: int, recovers: bool
) -> None:
    monkeypatch.setenv("OPEN_SWE_BACKEND_URL", "https://backend.invalid")
    sleep = AsyncMock()
    monkeypatch.setattr("rt.backend.asyncio.sleep", sleep)
    request = httpx2.Request("POST", "https://backend.invalid")
    # Only a call the backend could not have run is resent, or one that is safe to repeat.
    unavailable = [httpx2.Response(503, request=request), httpx2.ConnectError("secret-token")]
    if endpoint.delivery == "idempotent":
        unavailable.append(httpx2.Response(502, request=request))
    client = MagicMock(is_closed=False)
    client.post = AsyncMock(
        side_effect=[unavailable[i % len(unavailable)] for i in range(failures)]
        + [httpx2.Response(200, request=request, json={"update": {"prepared": True}})]
    )
    monkeypatch.setattr("rt.backend.httpx2.AsyncClient", lambda **kwargs: client)
    backend = _run_backend()

    def send() -> Awaitable[JsonValue]:
        return backend.post(endpoint.path, {}, timeout=1.0, delivery=endpoint.delivery)

    if recovers:
        assert await send() == {"update": {"prepared": True}}
        assert sum(wait.args[0] for wait in sleep.await_args_list) >= 120
    else:
        with pytest.raises(BackendUnavailableError):
            await send()
    assert client.post.await_count == min(failures + 1, 11)


@pytest.mark.parametrize("hook", ["prepare", "before_model", "after_model"])
async def test_an_unreachable_backend_fails_the_run(
    monkeypatch: pytest.MonkeyPatch, hook: str
) -> None:
    monkeypatch.setenv("OPEN_SWE_BACKEND_URL", "https://backend.invalid")
    monkeypatch.setattr("rt.middleware.run_id", lambda: "run-1")
    backend = _run_backend(invocation_id="invocation-1")

    async def phase(name: str, body: dict[str, JsonValue]) -> BaseModel:
        if name == hook:
            raise BackendUnavailableError(f"{hook} failed")
        assert name != "after_agent"
        return PhaseResult()

    _answering(monkeypatch, backend, phase)
    graph = create_agent(
        model=FakeListChatModel(responses=["done"]),
        middleware=[BackendRunMiddleware(backend)],
    )

    with pytest.raises(BackendUnavailableError):
        await graph.ainvoke({"messages": [("user", "hello")]})


_INBOX = InboxSpec(events=True, surface="slack")


async def test_the_inbox_names_only_the_run_and_hands_back_what_the_model_answers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPEN_SWE_BACKEND_URL", "https://backend.invalid")
    monkeypatch.setattr("rt.middleware.run_id", lambda: "run-1")
    # Nothing waits long for the receipt, which the backend takes only after the next read.
    monkeypatch.setattr("rt.outbox.DRAIN_SECONDS", 0.05)
    backend = _run_backend(invocation_id="invocation-1", catalog=Catalog(inbox=_INBOX))
    report = HumanMessage(content="The worker finished", id="event-match:report-1")
    items = InboxItems(queued=[7], events=[UUID("0199a1b2-0000-7000-8000-000000000001")])
    reads: list[InboxRead] = []
    read_again, took = asyncio.Event(), asyncio.Event()
    received: list[InboxReceipt] = []
    planned: list[RunCall] = []

    async def phase(name: str, body: dict[str, JsonValue]) -> BaseModel:
        if name == "inbox":
            reads.append(InboxRead.model_validate(body))
            if len(reads) > 1:
                read_again.set()
            # What a receipt or the read itself names as received is owed no more.
            if received or reads[-1].received == items:
                return Inbox()
            return Inbox.model_validate(
                {
                    "messages": messages_to_dict([report]),
                    "update": {"reply_surface": "web"},
                    "items": items.model_dump(mode="json"),
                }
            )
        if name == "inbox_received":
            await read_again.wait()
            received.append(InboxReceipt.model_validate(body))
            took.set()
        if name == "before_model":
            planned.append(RunCall.model_validate(body))
        if name == "after_model" and len(planned) == 1:
            return PhaseResult(jump_to="model")
        return PhaseResult()

    _answering(monkeypatch, backend, phase)
    graph = create_agent(
        model=FakeListChatModel(responses=["first", "second"]),
        middleware=[BackendRunMiddleware(backend)],
    )

    result = await graph.ainvoke({"messages": [("user", "hello")]})
    await asyncio.wait_for(took.wait(), 5)

    # The run and how its catalog reads the inbox; never its conversation or state.
    assert [(read.run_id, read.spec) for read in reads] == [("run-1", _INBOX)] * 2
    assert messages_from_dict(planned[0].messages)[-1].id == "event-match:report-1"
    assert planned[0].state["reply_surface"] == "web"
    assert [message.id for message in result["messages"]].count("event-match:report-1") == 1
    assert [receipt.items for receipt in received] == [items]
    # The next read names what the unsent receipt holds, so it cannot hand it back.
    assert reads[1].received == items


async def test_a_run_the_phase_ends_leaves_the_inbox_queued(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A model-call limit ends the run before any call answers the follow-up: it stays queued."""
    monkeypatch.setenv("OPEN_SWE_BACKEND_URL", "https://backend.invalid")
    monkeypatch.setattr("rt.middleware.run_id", lambda: "run-1")
    backend = _run_backend(invocation_id="invocation-1", catalog=Catalog(inbox=_INBOX))
    follow_up = HumanMessage(content="One more thing", id="queued-1")
    called: list[str] = []

    async def phase(name: str, body: dict[str, JsonValue]) -> BaseModel:
        called.append(name)
        if name == "inbox":
            return Inbox.model_validate(
                {"messages": messages_to_dict([follow_up]), "items": {"queued": [7]}}
            )
        if name == "before_model":
            return BeforeModel(jump_to="end")
        return PhaseResult()

    _answering(monkeypatch, backend, phase)
    graph = create_agent(
        model=FakeListChatModel(responses=["unused"]),
        middleware=[BackendRunMiddleware(backend)],
    )

    result = await graph.ainvoke({"messages": [("user", "hello")]})

    assert "inbox_received" not in called
    assert "queued-1" not in [message.id for message in result["messages"]]


async def test_a_moved_scout_stops_before_another_model_or_tool_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPEN_SWE_BACKEND_URL", "https://backend.invalid")
    monkeypatch.setattr("rt.middleware.run_id", lambda: "run-1")
    monkeypatch.setattr("rt.tools.run_id", lambda: "run-1")
    monkeypatch.setattr(GenericFakeChatModel, "bind_tools", lambda self, *args, **kwargs: self)
    backend = _run_backend(invocation_id="turn")
    operations: list[str] = []

    async def phase(name: str, body: dict[str, JsonValue]) -> BaseModel:
        if name == "prepare":
            return PhaseResult(update={"scout_nothing_to_plan": False})
        if name == "operation":
            operations.append(str(body["name"]))
            assert operations == ["walkthrough_describe_other"]
            return OperationResult(
                content='{"success": false, "error": "The pull request moved on. Stop."}',
                state={"scout_nothing_to_plan": True},
            )
        if name == "before_model":
            state = RunCall.model_validate(body).state
            return BeforeModel(jump_to="end" if state.get("scout_nothing_to_plan") else None)
        return PhaseResult()

    _answering(monkeypatch, backend, phase)
    tools = [
        operation_tool(
            ToolSpec(name=name, description=name, parameters={"type": "object"}), backend
        )
        for name in (
            "walkthrough_describe_other",
            "walkthrough_plan_chunk",
            "check_async_task",
            "start_async_task",
        )
    ]
    graph = create_agent(
        model=GenericFakeChatModel(
            messages=iter(
                [
                    AIMessage(
                        content="",
                        tool_calls=[
                            ToolCall(name="walkthrough_describe_other", args={}, id="call-1")
                        ],
                    )
                ]
            )
        ),
        tools=tools,
        middleware=[BackendRunMiddleware(backend)],
    )

    result = await graph.ainvoke({"messages": [("user", "Plan the walkthrough")]})

    assert operations == ["walkthrough_describe_other"]
    assert isinstance(result["messages"][-1], ToolMessage)
    assert result["messages"][-1].tool_call_id == "call-1"


async def test_the_git_baseline_runs_alongside_the_first_model_call_and_before_its_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPEN_SWE_BACKEND_URL", "https://backend.invalid")
    monkeypatch.setattr("rt.middleware.run_id", lambda: "run-1")
    monkeypatch.setattr("rt.tools.run_id", lambda: "run-1")
    monkeypatch.setattr(GenericFakeChatModel, "bind_tools", lambda self, *args, **kwargs: self)
    backend = _run_backend(invocation_id="turn")
    baseline = {"git_trace_baseline": {"invocation": "turn", "snapshot": None}}
    model_called = asyncio.Event()
    order: list[str] = []
    reported_git: list[dict[str, JsonValue]] = []

    async def phase(name: str, body: dict[str, JsonValue]) -> BaseModel:
        if name == "git_baseline":
            # Only the model call lets the snapshot finish: preparation must not wait on it.
            await asyncio.wait_for(model_called.wait(), 5)
            order.append("baseline")
            return GitBaseline(update=baseline)
        if name == "operation":
            order.append("tool")
            return OperationResult(content="ran")
        if name == "tool_started":
            reported_git.append(ToolCallReport.model_validate(body).git)
        return PhaseResult()

    class Model(AgentMiddleware):
        async def awrap_model_call(
            self, request: ModelRequest, handler: Callable[[ModelRequest], Awaitable[ModelResponse]]
        ) -> ModelResponse:
            order.append("model")
            model_called.set()
            return await handler(request)

    _answering(monkeypatch, backend, phase)
    run = ToolSpec(name="run", description="run", parameters={"type": "object"})
    middleware = cast(
        list[AgentMiddleware],
        [BackendRunMiddleware(backend), RuntimeToolsMiddleware(backend, ()), Model()],
    )
    graph = create_agent(
        model=GenericFakeChatModel(
            messages=iter(
                [
                    AIMessage(content="", tool_calls=[ToolCall(name="run", args={}, id="call-1")]),
                    AIMessage(content="done"),
                ]
            )
        ),
        tools=[operation_tool(run, backend)],
        middleware=middleware,
    )

    await graph.ainvoke({"messages": [("user", "Change something")]})
    await backend.outbox.drain()

    assert order[:3] == ["model", "baseline", "tool"]
    # What the backend measures the call's changes against.
    assert reported_git == [baseline]


async def test_reports_the_backend_never_took_go_out_first_from_the_runs_checkpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A run whose reports the backend never took keeps them in its checkpoint: the next
    process on the thread sends them, under the same keys, before its own."""
    from rt.middleware import ReportRepliesMiddleware
    from rt.outbox import OUTBOX_KEY

    monkeypatch.setenv("OPEN_SWE_BACKEND_URL", "https://backend.invalid")
    for module in ("rt.middleware", "rt.tools"):
        monkeypatch.setattr(f"{module}.run_id", lambda: "run-1")
    monkeypatch.setattr("rt.outbox._RETRY_SECONDS", (0.0,))
    monkeypatch.setattr("rt.outbox._GIVE_UP_SECONDS", 0.0)
    monkeypatch.setattr("rt.outbox.DRAIN_SECONDS", 0.05)
    monkeypatch.setattr(GenericFakeChatModel, "bind_tools", lambda self, *args, **kwargs: self)
    checkpointer = InMemorySaver()
    config: RunnableConfig = {"configurable": {"thread_id": "thread-1"}}

    payload = "large-tool-result:" + "x" * 100_000

    @tool
    async def look() -> str:
        """Look around."""
        return payload

    def run(backend: OpenSweBackend, *replies: AIMessage) -> Awaitable[dict[str, object]]:
        graph = create_agent(
            model=GenericFakeChatModel(messages=iter(replies)),
            tools=[look],
            middleware=cast(
                "list[AgentMiddleware]",
                [
                    BackendRunMiddleware(backend),
                    ReportRepliesMiddleware(backend),
                    RuntimeToolsMiddleware(backend, ()),
                ],
            ),
            checkpointer=checkpointer,
        )
        return graph.ainvoke({"messages": [HumanMessage(content="look", id="human-1")]}, config)

    down = _run_backend(invocation_id="invocation-1")

    async def unreachable(path: str, body: dict[str, JsonValue], **kwargs: object) -> JsonValue:
        if path == REPORTS.path:
            raise BackendUnavailableError("reports failed (ConnectError)")
        return PhaseResult().model_dump(mode="json")

    monkeypatch.setattr(down, "post", unreachable)
    called = AIMessage(
        content="", id="ai-1", tool_calls=[ToolCall(name="look", args={}, id="call-1")]
    )
    await run(down, called, AIMessage(content="done", id="ai-2"))

    up = _run_backend(invocation_id="invocation-1")
    sent: list[tuple[str, str]] = []
    results: list[dict[str, JsonValue]] = []

    async def taking(path: str, body: dict[str, JsonValue], **kwargs: object) -> JsonValue:
        if path != REPORTS.path:
            return PhaseResult().model_dump(mode="json")
        items = Reports.model_validate(body).items
        sent.extend((item.endpoint, item.key) for item in items)
        results.extend(item.request.result for item in items if item.endpoint == "tool_result")
        return ReportsTaken(replies=[{} for _ in items]).model_dump(mode="json")

    monkeypatch.setattr(up, "post", taking)
    left_over = await checkpointer.aget_tuple(config)
    assert left_over is not None
    carried = left_over.checkpoint["channel_values"][OUTBOX_KEY]
    assert payload not in json.dumps(carried)
    assert any(
        isinstance(message, ToolMessage) and message.content == payload
        for message in left_over.checkpoint["channel_values"]["messages"]
    )
    await run(up, AIMessage(content="again", id="ai-3"))

    # The first run's reports, in the order it made them, then the second's.
    assert [endpoint for endpoint, _ in sent] == [
        "model_result",
        "tool_started",
        "tool_result",
        "model_result",
        "model_result",
    ]
    assert {key for _, key in sent[:4]} == set(carried)
    assert messages_from_dict(results)[0].content == payload
    after = await checkpointer.aget_tuple(config)
    assert after is not None
    assert not after.checkpoint["channel_values"].get(OUTBOX_KEY)


@pytest.mark.parametrize("namespace", [[], ["task-1"]])
async def test_outbox_keeps_results_when_checkpoint_messages_cannot_restore_them(
    monkeypatch: pytest.MonkeyPatch, namespace: list[str]
) -> None:
    from rt.outbox import OUTBOX_KEY, Outbox, carry

    monkeypatch.setenv("OPEN_SWE_BACKEND_URL", "https://backend.invalid")
    backend = _run_backend()
    received: list[dict[str, JsonValue]] = []

    async def taking(path: str, body: dict[str, JsonValue], **kwargs: object) -> JsonValue:
        items = Reports.model_validate(body).items
        received.extend(item.request.result for item in items if item.endpoint == "tool_result")
        return ReportsTaken(replies=[{} for _ in items]).model_dump(mode="json")

    monkeypatch.setattr(backend, "post", taking)
    result = ToolMessage(content="original result", tool_call_id="call-1", id="result-1")
    backend.outbox.finished(
        ToolCallReport(run_id="run-1", name="look", tool_call_id="call-1", namespace=namespace),
        result,
    )
    state: dict[str, object] = {"messages": [result]}
    saved = backend.outbox.carried(state)
    assert saved is not None
    if not namespace:
        assert "original result" not in json.dumps(saved)
    else:
        assert "original result" in json.dumps(saved)
    state[OUTBOX_KEY] = saved
    state["messages"] = []
    expanded = backend.outbox.carried(state)
    state[OUTBOX_KEY] = carry(saved, expanded)
    assert "original result" in json.dumps(state[OUTBOX_KEY])
    restored = Outbox(backend)
    restored.adopt(state)
    await restored.drain()
    await backend.outbox.drain()
    assert received == messages_to_dict([result]) * 2
    if not namespace:
        # A process that never held the result, on a checkpoint that no longer holds it,
        # lets the report go rather than failing every later step of the thread.
        orphaned = Outbox(backend)
        assert orphaned.carried({"messages": [], OUTBOX_KEY: saved}) == dict.fromkeys(saved)


class _RecordingAnthropic(ChatAnthropic):
    sent: list[BaseMessage] = []

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: object,
    ) -> ChatResult:
        self.sent.extend(messages)
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content="done"))])


async def test_the_model_the_backend_names_gets_a_replayable_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPEN_SWE_BACKEND_URL", "https://backend.invalid")
    monkeypatch.setattr("rt.middleware.run_id", lambda: "run-1")
    named = _RecordingAnthropic(model_name="claude-opus-5-5", api_key="test-key", sent=[])
    monkeypatch.setattr("rt.middleware.build_model", lambda spec, use_gateway: named)
    backend = _run_backend(invocation_id="invocation-1")

    async def phase(name: str, body: dict[str, JsonValue]) -> BaseModel:
        if name != "before_model":
            return PhaseResult()
        return BeforeModel(
            call=ModelCall(
                system_prompt="You are Open SWE.",
                model=ModelSpec(model_id="anthropic:claude-opus-5-5", kwargs={}),
            )
        )

    _answering(monkeypatch, backend, phase)
    # As the agent orders them: the guards inside the middleware that names the model.
    middleware = cast(
        list[AgentMiddleware],
        [BackendRunMiddleware(backend), *model_call_guards()],
    )
    graph = create_agent(model=FakeListChatModel(responses=["unused"]), middleware=middleware)
    reply = AIMessage(
        content=[
            {"type": "thinking", "signature": "sig", "thinking": ""},
            {"type": "text", "text": "hello"},
        ]
    )

    await graph.ainvoke({"messages": [HumanMessage("hi"), reply, HumanMessage("again")]})

    assert [m.content for m in named.sent if isinstance(m, AIMessage)] == [
        [{"type": "text", "text": "hello"}]
    ]


class _PayloadAnthropic(ChatAnthropic):
    payloads: list[dict[str, object]] = []

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: object,
    ) -> ChatResult:
        self.payloads.append(self._get_request_payload(messages, stop=stop, **kwargs))
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content="done"))])


async def test_the_anthropic_model_the_backend_names_caches_for_an_hour(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPEN_SWE_BACKEND_URL", "https://backend.invalid")
    monkeypatch.setattr("rt.middleware.run_id", lambda: "run-1")
    named = _PayloadAnthropic(model_name="claude-opus-5-5", api_key="test-key", payloads=[])
    monkeypatch.setattr("rt.middleware.build_model", lambda spec, use_gateway: named)
    backend = _run_backend(invocation_id="invocation-1")

    async def phase(name: str, body: dict[str, JsonValue]) -> BaseModel:
        if name != "before_model":
            return PhaseResult()
        return BeforeModel(
            call=ModelCall(
                system_prompt="You are Open SWE.",
                thread_prompt="This thread is yours.",
                model=ModelSpec(model_id="anthropic:claude-opus-5-5", kwargs={}),
            )
        )

    _answering(monkeypatch, backend, phase)
    # deepagents installs its own 5m cache; the guards' must take its place.
    middleware = cast(
        list[AgentMiddleware],
        [
            BackendRunMiddleware(backend),
            RunPromptMiddleware(),
            ThreadPromptMiddleware(),
            *model_call_guards(),
        ],
    )
    graph = create_deep_agent(
        model=FakeListChatModel(responses=["unused"]),
        system_prompt="The graph's own instructions.",
        middleware=middleware,
    )

    await graph.ainvoke({"messages": [HumanMessage("hi")]})

    [payload] = named.payloads
    hour = {"type": "ephemeral", "ttl": "1h"}
    system = cast(list[dict[str, object]], payload["system"])
    tools = cast(list[dict[str, object]], payload["tools"])
    # The graph's prompt, the run's, the thread's and the conversation's end: Anthropic
    # rejects a request with more than four breakpoints.
    assert [block.get("cache_control") for block in system] == [hour, hour, hour]
    assert payload["cache_control"] == hour
    assert all("cache_control" not in tool for tool in tools)
    assert json.dumps(payload).count('"cache_control"') == 4
    assert '"5m"' not in json.dumps(payload)


class _StalledAnthropic(ChatAnthropic):
    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: object,
    ) -> ChatResult:
        await asyncio.sleep(60)
        raise AssertionError("cancelled by the deadline")


async def test_a_stalled_model_call_goes_to_the_fallback_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPEN_SWE_BACKEND_URL", "https://backend.invalid")
    monkeypatch.setattr("rt.middleware.run_id", lambda: "run-1")
    fallback = _RecordingAnthropic(model_name="claude-sonnet-5-5", api_key="test-key", sent=[])
    built = {
        "anthropic:claude-opus-5-5": _StalledAnthropic(model_name="claude-opus-5-5", api_key="k"),
        "anthropic:claude-sonnet-5-5": fallback,
    }
    monkeypatch.setattr("rt.middleware.build_model", lambda spec, use_gateway: built[spec.model_id])
    backend = _run_backend(invocation_id="invocation-1")

    async def phase(name: str, body: dict[str, JsonValue]) -> BaseModel:
        if name != "before_model":
            return PhaseResult()
        return BeforeModel(
            call=ModelCall(
                system_prompt="You are Open SWE.",
                model=ModelSpec(model_id="anthropic:claude-opus-5-5", kwargs={}),
                fallback=ModelSpec(model_id="anthropic:claude-sonnet-5-5", kwargs={}),
            )
        )

    _answering(monkeypatch, backend, phase)
    middleware = cast(
        list[AgentMiddleware],
        [
            BackendRunMiddleware(backend),
            *model_call_guards(timeout_seconds=1.0),
        ],
    )
    graph = create_agent(model=FakeListChatModel(responses=["unused"]), middleware=middleware)

    result = await graph.ainvoke({"messages": [HumanMessage("hi")]})

    assert result["messages"][-1].content == "done"
    assert fallback.sent


@pytest.mark.parametrize("selection", ["primary", "route", "vision", "fallback"])
async def test_turn_model_tracks_the_selected_call_without_relabeling_llm_spans(
    monkeypatch: pytest.MonkeyPatch, selection: str
) -> None:
    monkeypatch.setenv("OPEN_SWE_BACKEND_URL", "https://backend.invalid")
    monkeypatch.setattr("rt.middleware.run_id", lambda: "run-1")
    primary = ModelSpec(model_id="anthropic:primary", kwargs={})
    routed = ModelSpec(model_id="anthropic:routed", kwargs={})
    vision = ModelSpec(model_id="anthropic:vision", kwargs={})
    fallback = ModelSpec(model_id="anthropic:fallback", kwargs={})
    built: dict[str, ChatAnthropic] = {
        spec.model_id: _RecordingAnthropic(model_name=spec.model_id, api_key="test", sent=[])
        for spec in (primary, routed, vision, fallback)
    }
    if selection == "fallback":
        built[routed.model_id] = _StalledAnthropic(model_name=routed.model_id, api_key="test")
    monkeypatch.setattr("rt.middleware.build_model", lambda spec, use_gateway: built[spec.model_id])
    backend = _run_backend(
        invocation_id="turn",
        catalog=Catalog(models=RunModels(model=primary, subagent_model=primary, use_gateway=False)),
    )

    async def phase(name: str, body: dict[str, JsonValue]) -> BaseModel:
        if name == "before_model":
            return BeforeModel(
                call=ModelCall(
                    system_prompt="test",
                    model=None if selection == "primary" else routed,
                    vision=vision if selection == "vision" else None,
                    fallback=fallback if selection == "fallback" else None,
                )
            )
        return PhaseResult()

    _answering(monkeypatch, backend, phase)
    client = MagicMock(spec=Client)
    patches: dict[object, dict[str, object]] = {}

    def update(run_id: object, **kwargs: object) -> None:
        patches[run_id] = deepcopy(kwargs)

    client.update_run.side_effect = update
    graph = create_agent(
        model=FakeListChatModel(responses=["unused"]),
        middleware=cast(
            list[AgentMiddleware],
            [BackendRunMiddleware(backend), *model_call_guards(timeout_seconds=0.05)],
        ),
    )
    message = (
        HumanMessage(content=[{"type": "image_url", "image_url": {"url": "https://image.invalid"}}])
        if selection == "vision"
        else HumanMessage("hi")
    )
    with tracing_context(enabled=True):
        result = await graph.ainvoke(
            {"messages": [message]},
            {
                "callbacks": [LangChainTracer(client=client, project_name="test")],
                "metadata": {"invocation_id": "turn"},
            },
        )
    assert result["messages"][-1].content == "done"
    created = {call.kwargs["id"]: call.kwargs for call in client.create_run.call_args_list}
    root_id = next(run_id for run_id, run in created.items() if not run.get("parent_run_id"))
    expected = {"primary": primary, "route": routed, "vision": vision, "fallback": fallback}
    assert _run_metadata(patches[root_id])["model"] == expected[selection].model_id
    llms = [run_id for run_id, run in created.items() if run["run_type"] == "llm"]
    assert len(llms) == (2 if selection == "fallback" else 1)
    expected_calls = (
        [routed.model_id, fallback.model_id]
        if selection == "fallback"
        else [expected[selection].model_id]
    )
    assert [
        _run_metadata(patches.get(run_id, created[run_id]))["model"] for run_id in llms
    ] == expected_calls


async def test_a_stalled_subagent_call_answers_with_the_timeout() -> None:
    # Its `task` call hands this answer back; a raise would end the delegating run.
    subagent = create_agent(
        model=_StalledAnthropic(model_name="claude-opus-5-5", api_key="test-key"),
        middleware=model_call_guards(timeout_seconds=0.05, subagent=True),
    )

    result = await subagent.ainvoke({"messages": [HumanMessage("look into it")]})

    answer = result["messages"][-1]
    assert isinstance(answer, AIMessage)
    assert "timed out" in answer.text
    assert "ModelCallTimeoutError" in answer.text


async def test_a_failing_backend_hook_fails_the_run_with_what_it_raised(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPEN_SWE_BACKEND_URL", "https://backend.invalid")
    request = httpx2.Request("POST", "https://backend.invalid")
    failure = {"error": "RuntimeError", "message": "no sandbox provider"}
    client = MagicMock(is_closed=False)
    client.post = AsyncMock(return_value=httpx2.Response(500, request=request, json=failure))
    monkeypatch.setattr("rt.backend.httpx2.AsyncClient", lambda **kwargs: client)

    with pytest.raises(BackendCallError, match="prepare failed: RuntimeError: no sandbox provider"):
        await call(_run_backend(), PREPARE, Prepare())


@pytest.mark.parametrize(
    ("name", "arguments", "checked", "remote"),
    [
        ("execute", {"command": "cd /workspace/repo && git push origin HEAD"}, True, False),
        ("execute", {"command": "bash -c 'gh pr create --fill'"}, True, False),
        (
            "execute",
            {"command": "curl -X POST https://api.github.com/repos/o/r/pulls"},
            True,
            False,
        ),
        ("execute", {"command": 'g"h" pr create --fill'}, True, False),
        ("execute", {"command": "git pu''sh origin HEAD"}, True, False),
        ("execute", {"command": "git pu\\\nsh origin HEAD"}, True, False),
        ("read_file", {"file_path": "/skills/deploy/SKILL.md"}, False, True),
        ("execute", {"command": "uv run pytest -q tests/unit"}, False, False),
        ("read_file", {"file_path": "/workspace/repo/README.md"}, False, False),
    ],
)
async def test_box_tools_ask_the_guards_and_reach_the_backends_paths(
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    arguments: dict[str, str],
    checked: bool,
    remote: bool,
) -> None:
    """A command the push guards may stop is put to them first; a backend path is read there."""
    monkeypatch.setenv("OPEN_SWE_BACKEND_URL", "https://backend.invalid")
    for module in ("rt.middleware", "rt.tools", "rt.file_tools"):
        monkeypatch.setattr(f"{module}.run_id", lambda: "run-1")
    backend = _run_backend(
        catalog=Catalog(
            files=FilesSpec(backend_paths=["/skills/"], guards=["shell_commands"]), sandbox=_IN_BOX
        )
    )
    checks = AsyncMock(return_value=ShellVerdict(allowed=True).model_dump(mode="json"))
    monkeypatch.setattr(backend, "post", checks)
    monkeypatch.setattr(backend.outbox, "started", MagicMock())
    monkeypatch.setattr(backend.outbox, "finished", MagicMock())
    served = MagicMock()
    served.name = "read_file"
    served.ainvoke = AsyncMock(return_value=ToolMessage(content="skill", tool_call_id="call-1"))
    runtime = ToolRuntime(
        state={"messages": []},
        context=None,
        config={},
        stream_writer=lambda event: None,
        tool_call_id="call-1",
        store=None,
    )
    request = ToolCallRequest(
        tool_call={"name": name, "args": arguments, "id": "call-1", "type": "tool_call"},
        tool=None,
        state={"messages": []},
        runtime=runtime,
    )
    in_box = AsyncMock(return_value=ToolMessage(content="ran", tool_call_id="call-1"))
    monkeypatch.setattr("rt.file_tools.remote_file_tools", lambda *_: {"read_file": served})
    files = FileToolsMiddleware(backend, 300, remote_files=MagicMock())

    await files.awrap_tool_call(request, in_box)
    await backend.outbox.drain()

    assert checks.await_count == int(checked)
    assert served.ainvoke.await_count == int(remote)
    assert in_box.await_count == int(not remote)


@pytest.mark.parametrize("next_trace_available", [True, False])
async def test_turn_reconciliation_reaches_runtime_root_llm_and_nested_calls(
    monkeypatch: pytest.MonkeyPatch, next_trace_available: bool
) -> None:
    monkeypatch.setenv("OPEN_SWE_BACKEND_URL", "https://backend.invalid")
    monkeypatch.setattr("rt.middleware.run_id", lambda: "run-1")
    monkeypatch.setenv("LANGSMITH_REVISION_ID", "runtime-sha")
    monkeypatch.setenv("MDA_DEPLOYMENT_NAME", "open-swe-mda-prod")
    actor = {
        "ls_repo_provider": "github",
        "ls_attribution_identifier": "trigger",
        "LANGSMITH_AGENT_VERSION": "backend-sha",
    }
    final = {**actor, "ls_git_repo": "org/actual", "ls_git_branch": "Exact/PR-head"}
    backend = _run_backend(invocation_id="turn", catalog=Catalog(trace_metadata=actor))

    @tool
    async def nested_call(text: str) -> str:
        """Call a nested runnable."""
        TurnTrace.record_evidence(
            {
                **_evidence("work", "work", "org/actual", "local", 1),
                **_evidence("pr", "pr-later", "org/later", "later/head", 2),
                **_evidence("pr", "pr-first", "org/actual", final["ls_git_branch"], 3),
            }
        )
        return await RunnableLambda(lambda value: value).ainvoke(text)

    async def phase(name: str, body: dict[str, JsonValue]) -> BaseModel:
        if name == "prepare":
            return PhaseResult(update={"git_trace_baseline": None})
        if name == "git_baseline":
            return GitBaseline(
                update={"git_trace_baseline": {"invocation": "turn", "snapshot": None}}
            )
        if name == "before_model":
            assert isinstance(state := body["state"], dict)
            assert "git_trace_work:previous" not in state
            await nested_call.ainvoke({"text": "nested"})
            return BeforeModel(update=_evidence("work", "work", "org/actual", "local", 1))
        if name == "after_agent":
            # What the backend reconciles from that evidence: the earliest PR target first.
            return AfterAgent(
                trace_metadata={
                    **final,
                    "open_swe_git_targets": [
                        {"repo": "org/actual", "branch": final["ls_git_branch"]},
                        {"repo": "org/later", "branch": "later/head"},
                    ],
                },
                update={"git_trace_baseline": None},
            )
        return PhaseResult()

    _answering(monkeypatch, backend, phase)
    client = MagicMock(spec=Client)
    updates: list[tuple[object, dict[str, object]]] = []

    def update(run_id: object, **kwargs: object) -> None:
        updates.append((run_id, deepcopy(kwargs)))

    client.update_run.side_effect = update
    tracer = LangChainTracer(client=client, project_name="test")
    parent = RunTree(name="external request", run_type="chain", ls_client=client)
    monkeypatch.setattr(FakeListChatModel, "bind_tools", lambda self, *args, **kwargs: self)
    graph = create_deep_agent(
        model=FakeListChatModel(responses=["done"]),
        middleware=[BackendRunMiddleware(backend)],
        checkpointer=InMemorySaver(),
    )
    config: RunnableConfig = {
        "configurable": {"thread_id": "trace-test"},
        "callbacks": [tracer],
        "metadata": {
            "invocation_id": "turn",
            "existing_key": "preserved",
            "revision_id": "queued-sha",
            "LANGSMITH_AGENT_VERSION": "queued-agent-sha",
            "environment": "queued-environment",
        },
    }
    with tracing_context(enabled=True, parent=parent):
        await graph.ainvoke(
            {
                "messages": [("user", "hello")],
                "open_swe_state": {
                    "kept": True,
                    "git_trace_work:previous": {"invocation": "previous", "targets": []},
                },
            },
            config,
        )
    saved = await graph.aget_state(config)
    assert saved.values["open_swe_state"] == {"kept": True, "git_trace_baseline": None}
    assert "ls_git_repo" not in parent.metadata
    created = {call.kwargs["id"]: call.kwargs for call in client.create_run.call_args_list}
    assert any(run["run_type"] == "llm" for run in created.values())
    assert any(run["run_type"] == "tool" for run in created.values())
    reconciled = {
        run_id: metadata
        for run_id, patch in updates
        if (metadata := _run_metadata(patch)).get("ls_git_repo") == "org/actual"
    }
    attributed = {
        run_id
        for run_id, run in created.items()
        if run["run_type"] in {"llm", "tool"} or run.get("parent_run_id") == parent.id
    }
    assert attributed <= reconciled.keys()
    for run_id in attributed:
        metadata = reconciled[run_id]
        assert metadata.items() >= final.items()
        assert metadata["open_swe_git_targets"] == [
            {"repo": "org/actual", "branch": "Exact/PR-head"},
            {"repo": "org/later", "branch": "later/head"},
        ]
        assert metadata["existing_key"] == "preserved"
    [root_id] = [run_id for run_id, run in created.items() if run.get("parent_run_id") == parent.id]
    assert reconciled[root_id]["revision_id"] == "runtime-sha"
    assert reconciled[root_id]["LANGSMITH_AGENT_VERSION"] == "backend-sha"
    assert reconciled[root_id]["environment"] == "production"
    assert len({run["trace_id"] for run in created.values()}) == 1
    previous_update_count = len(updates)
    final["ls_git_branch"] = "Next/turn"
    if not next_trace_available:

        def unavailable_trace() -> None:
            raise RuntimeError("trace lookup unavailable")

        monkeypatch.setattr(TurnTrace, "current_root", staticmethod(unavailable_trace))
    with tracing_context(enabled=True, parent=parent):
        await graph.ainvoke(
            {"messages": [("user", "next turn")]},
            {**config, "metadata": {"invocation_id": "turn"}},
        )
    assert all(run_id not in created for run_id, _ in updates[previous_update_count:])


@pytest.mark.parametrize("destination", ["project", "address"])
async def test_backfill_preserves_destination_timing_and_outputs(
    monkeypatch: pytest.MonkeyPatch, destination: str
) -> None:
    patches: list[dict[str, object]] = []

    def capture(client: Client, data: dict[str, object], **kwargs: object) -> None:
        patches.append(deepcopy(data))

    monkeypatch.setattr(Client, "_update_run", capture)
    started = datetime(2026, 1, 1, tzinfo=UTC)
    ended = started + timedelta(seconds=1)
    with closing(
        Client(api_key="test", api_url="https://trace.invalid", auto_batch_tracing=False)
    ) as client:
        root = RunTree(
            name="turn",
            run_type="chain",
            start_time=started,
            ls_client=client,
            extra={"metadata": {"invocation_id": "turn"}},
            project_name="source" if destination != "address" else None,
            address=agent_address("open-swe", "production") if destination == "address" else None,
        )
        child = root.create_child(
            name="model",
            run_type="llm",
            start_time=started,
            extra={"metadata": {"invocation_id": "turn"}},
        )
        child.end(outputs={"answer": "done"}, end_time=ended)
        trace = TurnTrace()
        trace.root = root
        await trace.reconcile({"ls_git_repo": "org/repo", "ls_git_branch": "feature"})
    child_patch = next(patch for patch in patches if patch["id"] == child.id)
    for patch in patches:
        assert patch["end_time"] == ended.isoformat()
        assert "inputs" not in patch
        assert patch["outputs"] == {"answer": "done"}
        assert isinstance(extra := patch["extra"], dict)
        assert extra["metadata"]["ls_git_repo"] == "org/repo"
        assert extra["metadata"]["ls_git_branch"] == "feature"
    assert len(patches) == 1
    if destination == "project":
        assert child_patch["session_name"] == "source"
    else:
        assert child_patch["address"] == "lrn:agents/open-swe/environments/production"
    assert child.outputs == {"answer": "done"}
    assert root.end_time is None
    assert root.metadata["ls_git_repo"] == child.metadata["ls_git_repo"] == "org/repo"


@pytest.mark.parametrize("failure", ["lookup", "evidence", "export"])
async def test_optional_trace_failures_do_not_change_successful_turn_output(
    monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    monkeypatch.setenv("OPEN_SWE_BACKEND_URL", "https://backend.invalid")
    monkeypatch.setattr("rt.middleware.run_id", lambda: "run-1")
    backend = _run_backend(invocation_id="turn")

    def unavailable_trace() -> None:
        raise RuntimeError("trace lookup unavailable")

    if failure == "lookup":
        monkeypatch.setattr(TurnTrace, "current_root", staticmethod(unavailable_trace))

    async def phase(name: str, body: dict[str, JsonValue]) -> BaseModel:
        if name == "before_model":
            TurnTrace.record_evidence({"git_trace_pr:call": {"invocation": "turn", "targets": []}})
        if failure == "evidence" and name == "after_model":
            root = TurnTrace.current_root()
            assert root is not None
            root.metadata["open_swe_git_evidence"] = "invalid"
        return AfterAgent(trace_metadata={"ls_git_repo": "org/actual"})

    export_failed = False

    def update(run_id: object, **kwargs: object) -> None:
        nonlocal export_failed
        extra = kwargs.get("extra")
        if (
            failure == "export"
            and not export_failed
            and isinstance(extra, dict)
            and extra.get("metadata", {}).get("ls_git_repo") == "org/actual"
        ):
            export_failed = True
            raise RuntimeError("metadata exporter unavailable")

    _answering(monkeypatch, backend, phase)
    client = MagicMock(spec=Client)
    client.update_run.side_effect = update
    graph = create_agent(
        model=FakeListChatModel(responses=["done"]),
        middleware=[BackendRunMiddleware(backend)],
    )
    with tracing_context(enabled=True):
        result = await graph.ainvoke(
            {"messages": [("user", "hello")]},
            {
                "callbacks": [LangChainTracer(client=client, project_name="test")],
                "metadata": {"invocation_id": "turn"},
            },
        )
    assert result["messages"][-1].content == "done"
    assert export_failed == (failure == "export")


async def test_box_call_git_evidence_reaches_the_turn_after_its_run_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The backend answers a box call's report after the call's run closed; its evidence still counts."""
    monkeypatch.setenv("OPEN_SWE_BACKEND_URL", "https://backend.invalid")
    evidence = _evidence("work", "call-1", "org/actual", "work", 1)
    turn = RunTree(name="turn", run_type="chain", extra={"metadata": {"open_swe_git_evidence": {}}})
    roots: list[RunTree | None] = [turn]
    monkeypatch.setattr(TurnTrace, "current_root", staticmethod(lambda: roots[0]))
    backend = _run_backend()

    async def answer(name: str, body: dict[str, JsonValue]) -> BaseModel:
        return ToolResultReply(update=evidence)

    _answering(monkeypatch, backend, answer)

    backend.outbox.finished(
        ToolCallReport(name="execute", tool_call_id="call-1"),
        ToolMessage(content="ran", tool_call_id="call-1"),
    )
    roots[0] = None  # The call's run has closed: the turn is no longer found from here.
    await backend.outbox.drain()

    assert turn.metadata["open_swe_git_evidence"] == evidence


async def test_an_operation_code_mode_calls_is_reported_and_cited_under_its_eval_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Code mode calls tools past the tool-call chain, so the tool reports itself."""
    from rt.backend import reporting, subagent_of
    from runtime_api.operations import EvidenceReply

    monkeypatch.setenv("OPEN_SWE_BACKEND_URL", "https://backend.invalid")
    monkeypatch.setattr("rt.tools.run_id", lambda: "run-1")
    backend = _run_backend()
    answers = {
        "/agent-api/operation": OperationResult(content="found it"),
        "/agent-api/record_evidence": EvidenceReply(content="found it\n\nIncident evidence: ev-1"),
    }

    async def post(path: str, body: dict[str, object], **kwargs: object) -> object:
        return answers[path].model_dump(mode="json")

    monkeypatch.setattr(backend, "post", post)
    started = MagicMock()
    finished = MagicMock()
    monkeypatch.setattr(backend.outbox, "started", started)
    monkeypatch.setattr(backend.outbox, "finished", finished)
    search = operation_tool(
        ToolSpec(name="search_incidents", description="Search", parameters={"type": "object"}),
        backend,
        cites_evidence=True,
    )
    runtime = ToolRuntime(
        state={"messages": []},
        context=None,
        config={},
        stream_writer=lambda event: None,
        tool_call_id=None,
        store=None,
    )

    # As inside the eval call the agent's chain reports.
    assert search.coroutine is not None
    with subagent_of("eval-1"), reporting("eval-1"):
        result = await search.coroutine(runtime=runtime)
    await backend.outbox.drain()

    assert isinstance(result, ToolMessage)
    assert result.content == "found it\n\nIncident evidence: ev-1"
    [[report]] = [call.args for call in started.call_args_list]
    assert report.name == "search_incidents"
    assert report.namespace == ["eval-1"]
    [[_body, recorded]] = [call.args for call in finished.call_args_list]
    assert recorded.content == result.content


async def test_a_command_the_guards_refuse_is_recorded_with_their_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPEN_SWE_BACKEND_URL", "https://backend.invalid")
    for module in ("rt.middleware", "rt.tools", "rt.file_tools"):
        monkeypatch.setattr(f"{module}.run_id", lambda: "run-1")
    backend = _run_backend(
        catalog=Catalog(files=FilesSpec(guards=["shell_commands"]), sandbox=_IN_BOX)
    )
    refusal = ShellVerdict(allowed=False, refusal="pr_creation_fallback_blocked")
    monkeypatch.setattr(backend, "post", AsyncMock(return_value=refusal.model_dump(mode="json")))
    started = MagicMock()
    finished = MagicMock()
    monkeypatch.setattr(backend.outbox, "started", started)
    monkeypatch.setattr(backend.outbox, "finished", finished)
    runtime = ToolRuntime(
        state={"messages": []},
        context=None,
        config={},
        stream_writer=lambda event: None,
        tool_call_id="call-1",
        store=None,
    )
    request = ToolCallRequest(
        tool_call={
            "name": "execute",
            "args": {"command": "gh pr create --fill"},
            "id": "call-1",
            "type": "tool_call",
        },
        tool=None,
        state={"messages": []},
        runtime=runtime,
    )
    in_box = AsyncMock()
    files = FileToolsMiddleware(backend, 300, remote_files=MagicMock())

    refused = await files.awrap_tool_call(request, in_box)
    await backend.outbox.drain()

    assert isinstance(refused, ToolMessage) and refused.status == "error"
    in_box.assert_not_awaited()
    started.assert_called_once()
    [[_body, recorded]] = [call.args for call in finished.call_args_list]
    assert recorded.content == "pr_creation_fallback_blocked"


@pytest.mark.parametrize("excluded", [[], ["grep", "glob"]])
async def test_execute_searches_through_the_shell_where_grep_and_glob_are_excluded(
    monkeypatch: pytest.MonkeyPatch, excluded: list[str]
) -> None:
    """The model and code mode see an ``execute`` that sends searches to the shell, not
    to the ``grep`` and ``glob`` tools the run's catalog keeps from them."""
    from langchain.agents.middleware.types import ModelRequest
    from rt.remote_files import RemoteFiles, remote_file_tools

    monkeypatch.setenv("OPEN_SWE_BACKEND_URL", "https://backend.invalid")
    backend = _run_backend(catalog=Catalog(excluded=excluded))
    remote = RemoteFiles(backend, lambda: "run-1")
    tools = remote_file_tools(remote, "/")
    original = tools["execute"].description
    assert "Instead use the grep, glob tools" in original
    files = FileToolsMiddleware(backend, 300, remote_files=remote)
    request = ModelRequest(
        model=FakeListChatModel(responses=["done"]),
        messages=[],
        tools=list(tools.values()),
        state={"messages": []},
    )
    received = AsyncMock()

    await files.awrap_model_call(request, received)

    [offered] = received.call_args.args
    descriptions = [tool.description for tool in offered.tools if tool.name == "execute"]
    descriptions.extend(tool.description for tool in files.code_tools(["execute"]))
    assert len(descriptions) == 2
    for description in descriptions:
        if excluded:
            assert "`git grep -n` or `grep -rn`" in description
            assert "no separate grep/glob tools" in description
            assert "Instead use the grep, glob tools" not in description
            assert "Use glob tool instead" not in description
            assert "Use grep tool instead" not in description
        else:
            assert description == original


async def test_a_command_code_mode_runs_is_put_to_the_guards_too(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Code mode calls tools past the tool-call chain, so its shell tool checks each call itself."""
    from langchain.agents.middleware.types import ModelRequest
    from rt.remote_files import RemoteFiles

    monkeypatch.setenv("OPEN_SWE_BACKEND_URL", "https://backend.invalid")
    for module in ("rt.middleware", "rt.tools", "rt.file_tools"):
        monkeypatch.setattr(f"{module}.run_id", lambda: "run-1")
    backend = _run_backend(
        catalog=Catalog(files=FilesSpec(guards=["shell_commands"]), sandbox=_IN_BOX)
    )
    refusal = ShellVerdict(allowed=False, refusal="pr_creation_fallback_blocked")
    monkeypatch.setattr(backend, "post", AsyncMock(return_value=refusal.model_dump(mode="json")))
    monkeypatch.setattr(backend.outbox, "started", MagicMock())
    monkeypatch.setattr(backend.outbox, "finished", MagicMock())
    ran = AsyncMock(return_value="opened")

    @tool("execute")
    async def box_execute(command: str) -> str:
        """Run a command in the box."""
        return await ran(command)

    remote = RemoteFiles(backend, lambda: "run-1")
    files = FileToolsMiddleware(backend, 300, remote_files=remote)
    model_request = MagicMock(spec=ModelRequest)
    model_request.tools = [box_execute]
    await files.awrap_model_call(model_request, AsyncMock())
    [execute] = files.code_tools(["execute", "web_search"])
    runtime = ToolRuntime(
        state={"messages": []},
        context=None,
        config={},
        stream_writer=lambda event: None,
        tool_call_id="ptc_execute_1",
        store=None,
    )

    assert execute.coroutine is not None
    refused = await execute.coroutine(runtime=runtime, command="gh pr create --fill")

    assert isinstance(refused, ToolMessage) and refused.content == "pr_creation_fallback_blocked"
    ran.assert_not_awaited()


async def test_a_read_file_the_backend_serves_gets_the_read_checks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A /blobs/ file with an image's extension but not its bytes never reaches the model as one."""
    monkeypatch.setenv("OPEN_SWE_BACKEND_URL", "https://backend.invalid")
    for module in ("rt.middleware", "rt.tools"):
        monkeypatch.setattr(f"{module}.run_id", lambda: "run-1")
    backend = _run_backend(
        catalog=Catalog(
            files=FilesSpec(backend_paths=["/blobs/"], checks=["image_reads"]), sandbox=_IN_BOX
        )
    )
    monkeypatch.setattr(backend.outbox, "started", MagicMock())
    monkeypatch.setattr(backend.outbox, "finished", MagicMock())
    fake = ToolMessage(
        content=[
            {"type": "image", "base64": "eyJlcnJvciI6ICJleHBpcmVkIn0=", "mime_type": "image/png"}
        ],
        tool_call_id="call-1",
        name="read_file",
    )
    served = MagicMock()
    served.name = "read_file"
    served.ainvoke = AsyncMock(return_value=fake)
    runtime = ToolRuntime(
        state={"messages": []},
        context=None,
        config={},
        stream_writer=lambda event: None,
        tool_call_id="call-1",
        store=None,
    )
    request = ToolCallRequest(
        tool_call={
            "name": "read_file",
            "args": {"file_path": "/blobs/shot.png"},
            "id": "call-1",
            "type": "tool_call",
        },
        tool=None,
        state={"messages": []},
        runtime=runtime,
    )
    monkeypatch.setattr("rt.file_tools.remote_file_tools", lambda *_: {"read_file": served})
    files = FileToolsMiddleware(backend, 300, remote_files=MagicMock())

    message = await files.awrap_tool_call(request, AsyncMock())

    assert isinstance(message, ToolMessage)
    assert message.status == "error"
    assert "not a valid image" in message.content


async def test_a_run_without_an_mda_box_offloads_a_large_result_to_its_own_files(
    tmp_path: Path,
) -> None:
    files = LocalShellBackend(root_dir=tmp_path, virtual_mode=True)

    @tool
    def dump() -> str:
        """Print a lot."""
        return "x" * 100_000

    class NoInputs(BaseModel):
        pass

    async def no_inputs(_runtime: ManagedServerRuntime) -> NoInputs:
        return NoInputs()

    # A shared graph, as Open SWE's is: compiled for no one run.
    @shared_factory(no_inputs)
    def shared(_inputs: NoInputs) -> DeepAgentDefinition:
        return define_deep_agent(
            name="test-agent",
            model=_ToolCallingModel(
                messages=iter(
                    [
                        AIMessage(content="", tool_calls=[ToolCall(name="dump", args={}, id="c1")]),
                        AIMessage(content="done"),
                    ]
                )
            ),
            tools=[dump],
            sandbox=box(files),
        )

    agent = await compile_managed_agent_async(shared, None, report_telemetry=False)

    result = await agent.ainvoke({"messages": [("user", "dump")]})

    assert result["messages"][-1].content == "done"
    assert (tmp_path / "large_tool_results" / "c1").read_text() == "x" * 100_000


async def test_each_run_lists_only_the_skills_it_may_see_and_reads_them_here(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from deepagents.middleware.filesystem import FilesystemMiddleware
    from deepagents.middleware.skills import SkillsState
    from rt.skills import RunSkillsMiddleware, SkillFiles
    from runtime_api.loop import ToolResultReply
    from runtime_api.skills import RunSkills, SkillFile

    monkeypatch.setenv("OPEN_SWE_BACKEND_URL", "https://backend.invalid")
    for module in ("rt.middleware", "rt.tools", "rt.file_tools"):
        monkeypatch.setattr(f"{module}.run_id", lambda: "run-1")
    backend = _run_backend(
        catalog=Catalog(skill_sources=["/skills/", "/organization-skills/"], sandbox=_IN_BOX)
    )
    asked: list[str] = []

    async def answer(name: str, body: dict[str, JsonValue]) -> BaseModel:
        asked.append(name)
        if name != "skills":
            return ToolResultReply()
        deploy = "---\nname: deploy\ndescription: Ship it\n---\n\nRun make deploy.\n"
        return RunSkills(
            files=[SkillFile(path="/organization-skills/deploy/SKILL.md", content=deploy)]
        )

    _answering(monkeypatch, backend, answer)
    skill_files = SkillFiles(backend, lambda: "run-1")
    # A run that acted for someone listed their skill; this thread's checkpoint kept it.
    kept = cast(
        SkillsState,
        {
            "messages": [],
            "skills_metadata": [
                {"name": "personal", "description": "private", "path": "/skills/personal/SKILL.md"}
            ],
        },
    )

    listed = await RunSkillsMiddleware(backend, skill_files).abefore_agent(kept, MagicMock(), {})

    assert listed is not None
    assert [skill["name"] for skill in listed["skills_metadata"]] == ["deploy"]
    files = FileToolsMiddleware(
        backend,
        300,
        remote_files=MagicMock(),
        skills={tool.name: tool for tool in FilesystemMiddleware(backend=skill_files).tools},
    )
    runtime = ToolRuntime(
        state={"messages": []},
        context=None,
        config={},
        stream_writer=lambda event: None,
        tool_call_id="call-1",
        store=None,
    )
    in_box = AsyncMock()
    read = await files.awrap_tool_call(
        ToolCallRequest(
            tool_call={
                "name": "read_file",
                "args": {"file_path": "/organization-skills/deploy/SKILL.md"},
                "id": "call-1",
                "type": "tool_call",
            },
            tool=None,
            state={"messages": []},
            runtime=runtime,
        ),
        in_box,
    )
    await backend.outbox.drain()

    assert isinstance(read, ToolMessage) and "Run make deploy." in read.text
    in_box.assert_not_awaited()
    # Listed once for the run, and no file was read through the backend's files.
    assert asked.count("skills") == 1
    assert not [name for name in asked if name.startswith("files.")]


class _ToolCallingModel(GenericFakeChatModel):
    def bind_tools(self, tools: object, **kwargs: object) -> Self:
        return self


COORDINATOR_CATALOG = Catalog(skill_sources=["/skills/coordinator/"])


async def _launch_worker(
    monkeypatch: pytest.MonkeyPatch, client: MagicMock, *, also: Sequence[ToolCall] = ()
) -> tuple[list[ToolMessage], list[tuple[str, dict[str, object]]]]:
    """Run a coordinator that launches a worker through deepagents' ``start_async_task``."""
    from deepagents.middleware import async_subagents
    from rt.middleware import AsyncWorkerLaunchMiddleware

    monkeypatch.setenv("OPEN_SWE_BACKEND_URL", "https://backend.invalid")
    backend = _run_backend("coordinator-token", catalog=COORDINATOR_CATALOG)
    calls: list[tuple[str, dict[str, object]]] = []

    async def post(
        path: str,
        body: dict[str, JsonValue],
        *,
        timeout: float,
        delivery: Delivery,
        headers: Mapping[str, str] | None = None,
    ) -> JsonValue:
        name = path.rsplit("/", 1)[-1]
        calls.append((name, dict(body)))
        if name == "launch_worker":
            return {"grant": "worker-grant", "assignment": "Your assignment: Fix login"}
        return {"worker": {"worker_thread_id": body["worker_thread_id"], "task_id": "task-1"}}

    monkeypatch.setattr(backend, "post", post)
    launches = async_subagents.AsyncSubAgentMiddleware(
        async_subagents=[
            {
                "name": "general-purpose",
                "description": "A task worker.",
                "graph_id": "open-swe--general-purpose",
                # As `mda/agent.py` declares the worker.
                "notify_parent": False,
                "webhook": "https://backend.invalid/webhooks/run-complete?token=t",
            }
        ]
    )
    # The launch's in-process LangGraph client, the deployment's own Agent Server.
    monkeypatch.setattr(async_subagents._ClientCache, "get_async", lambda self, name: client)
    launch: ToolCall = {
        "name": "start_async_task",
        "args": {"description": "Fix login", "subagent_type": "general-purpose"},
        "id": "launch-1",
    }
    model = _ToolCallingModel(
        messages=iter([AIMessage("", tool_calls=[launch, *also]), AIMessage("Launched.")])
    )
    graph = create_agent(model, middleware=[AsyncWorkerLaunchMiddleware(backend), launches])
    values = await graph.ainvoke(
        {"messages": [("user", "fix login in the background")]},
        {
            "configurable": {"thread_id": "coordinator-thread", "assistant_id": "open-swe"},
            "metadata": {"assistant_id": "open-swe"},
        },
        # The launch middleware reads this context, though it declares no context type.
        context={"graph": "agent", "__run_token": "coordinator-token"},  # ty: ignore[invalid-argument-type]
    )
    # Open SWE's task messages report the worker; deepagents does not poll it too.
    assert not values.get("async_tasks")
    return [m for m in values["messages"] if isinstance(m, ToolMessage)], calls


async def test_a_launched_worker_carries_a_grant_never_its_coordinators_run_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = MagicMock()
    client.threads.create = AsyncMock(return_value={"thread_id": "worker-thread"})
    client.runs.create = AsyncMock(return_value={"run_id": "worker-run"})

    results, calls = await _launch_worker(
        monkeypatch,
        client,
        also=[
            {"name": "check_async_task", "args": {"task_id": "worker-thread"}, "id": "check-1"},
            # An update would interrupt the worker; Open SWE queues messages instead.
            {
                "name": "update_async_task",
                "args": {"task_id": "worker-thread", "message": "Stop"},
                "id": "update-1",
            },
        ],
    )

    assert client.runs.create.await_args is not None
    launch = client.runs.create.await_args.kwargs
    # The coordinator's catalog, which the worker's graph is built from until it adopts its own.
    assert launch["context"] == {
        "graph": "agent",
        "__worker_grant": "worker-grant",
        "__catalog": COORDINATOR_CATALOG.model_dump(mode="json"),
    }
    assert launch["input"] == {
        "messages": [{"role": "user", "content": "Your assignment: Fix login"}]
    }
    assert "coordinator-token" not in repr(client.runs.create.await_args)
    # The backend settles the launched run from its webhook, as it does every worker run.
    assert launch["webhook"] == "https://backend.invalid/webhooks/run-complete?token=t"
    # No parent assistant, so the worker never wakes the coordinator on MDA's own.
    assert client.threads.create.await_args is not None
    assert client.threads.create.await_args.kwargs["metadata"] == {
        "subagent_name": "general-purpose",
        "parent_thread_id": "coordinator-thread",
    }
    assert calls == [
        ("launch_worker", {"tool_call_id": "launch-1", "instructions": "Fix login"}),
        (
            "worker_launched",
            {
                "tool_call_id": "launch-1",
                "instructions": "Fix login",
                "worker_thread_id": "worker-thread",
                "worker_run_id": "worker-run",
            },
        ),
    ]
    by_call = {m.tool_call_id: m for m in results}
    assert "worker-thread" in str(by_call["launch-1"].content)
    assert by_call["check-1"].status == by_call["update-1"].status == "error"
    client.runs.create.assert_awaited_once()


def _mda_error(status: int) -> APIStatusError:
    error = {429: RateLimitError, 403: PermissionDeniedError}.get(status, InternalServerError)
    response = httpx.Response(status, request=httpx.Request("POST", "http://api/threads/runs"))
    return error(f"MDA answered {status}", response=response, body=None)


@pytest.mark.parametrize(
    ("failures", "launched"),
    [
        ([_mda_error(503)], True),
        ([httpx.ConnectError("refused"), _mda_error(429)], True),
        ([_mda_error(403)], False),
        ([_mda_error(503)] * 3, False),
    ],
)
async def test_a_worker_launch_mda_failed_to_start_is_made_again_while_it_may_pass(
    monkeypatch: pytest.MonkeyPatch, failures: list[Exception], launched: bool
) -> None:
    monkeypatch.setattr("rt.middleware._LAUNCH_RETRY_SECONDS", (0.0, 0.0))
    client = MagicMock()
    client.threads.create = AsyncMock(
        side_effect=[{"thread_id": f"thread-{n}"} for n in range(len(failures) + 1)]
    )
    client.runs.create = AsyncMock(side_effect=[*failures, {"run_id": "worker-run"}])

    [result], calls = await _launch_worker(monkeypatch, client)

    attempts = [body for hook, body in calls if hook == "launch_worker"]
    # Every attempt is the same launch, so one launch still registers one worker.
    assert attempts == [{"tool_call_id": "launch-1", "instructions": "Fix login"}] * len(attempts)
    if launched:
        assert len(attempts) == len(failures) + 1
        assert result.status == "success"
        assert f"thread-{len(failures)}" in str(result.content)
        assert [hook for hook, _ in calls].count("worker_launched") == 1
    else:
        # A refusal is final; a failure that may pass is tried three times in all.
        assert len(attempts) == min(len(failures), 3)
        assert result.status == "error"
        assert "did not launch" in str(result.content)
        assert "MDA answered" in str(result.content)
        assert "start_async_task again is safe" in str(result.content)
        assert "worker_launched" not in [hook for hook, _ in calls]


async def test_a_launched_worker_adopts_its_own_run_token_before_anything_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPEN_SWE_BACKEND_URL", "https://backend.invalid")
    monkeypatch.setattr("rt.middleware.run_id", lambda: "worker-run")
    request = httpx2.Request("POST", "https://backend.invalid")
    seen: list[tuple[str, str]] = []

    async def post(
        url: str, *, headers: dict[str, str], json: dict[str, object], **kwargs: object
    ) -> httpx2.Response:
        hook = url.rsplit("/", 1)[-1]
        seen.append((hook, headers["Authorization"]))
        if hook == "adopt":
            assert json == {
                "thread_id": "worker-thread",
                "run_id": "worker-run",
                "instructions": "Fix login",
            }
            adopted = {
                "run_token": "worker-token",
                "catalog": {"excluded": ["slack_reply"]},
                "invocation_id": "worker-invocation",
            }
            return httpx2.Response(200, request=request, json=adopted)
        return httpx2.Response(200, request=request, json={})

    client = MagicMock(is_closed=False)
    client.post = AsyncMock(side_effect=post)
    monkeypatch.setattr("rt.backend.httpx2.AsyncClient", lambda **kwargs: client)
    backend = _run_backend(None, worker_grant="worker-grant")
    graph = create_agent(
        model=FakeListChatModel(responses=["done"]),
        middleware=[BackendRunMiddleware(backend)],
    )

    await graph.ainvoke(
        {"messages": [("user", "Fix login")]}, {"configurable": {"thread_id": "worker-thread"}}
    )

    assert seen[0] == ("adopt", "Bearer worker-grant")
    # Its own catalog came with its run token: it is never asked for again.
    assert seen[1][0] == "prepare"
    assert "catalog" not in [hook for hook, _ in seen]
    assert backend.adopted is not None and backend.adopted.excluded == ["slack_reply"]
    assert all(bearer == "Bearer worker-token" for _, bearer in seen[1:])
    # Its replies are stamped with its own invocation, which its usage counts them by.
    assert backend.invocation_id == "worker-invocation"


@pytest.mark.parametrize(
    ("status", "message", "retries"),
    [
        (400, "No tool output found for function call call_1", False),
        (400, "No tool call found for function call output call_1", False),
        (400, "unexpected `tool_use_id` found in `tool_result` blocks", False),
        (400, "tool_use ids were found without `tool_result` blocks", False),
        (400, "context window exceeded", True),
        (500, "No tool output found for function call", True),
    ],
)
async def test_mda_does_not_retry_tool_pairing_errors(
    monkeypatch: pytest.MonkeyPatch, status: int, message: str, retries: bool
) -> None:
    from langchain.agents.middleware.types import ModelRequest, ModelResponse

    monkeypatch.setenv("OPEN_SWE_BACKEND_URL", "https://backend.invalid")
    model = FakeListChatModel(responses=["done"])
    monkeypatch.setattr("rt.middleware.build_model", lambda *args, **kwargs: model)
    response = httpx2.Response(status, request=httpx2.Request("POST", "https://provider.invalid"))
    error = (
        anthropic.BadRequestError(message, response=response, body={})
        if "tool_use" in message
        else openai.APIStatusError(message, response=response, body={})
    )
    handler = AsyncMock(side_effect=[error, ModelResponse(result=[AIMessage(content="fallback")])])
    state: RunState = {
        "messages": [],
        "open_swe_call": ModelCall(
            system_prompt="test",
            model=None,
            fallback=ModelSpec(model_id="fallback", kwargs={}),
            use_gateway=False,
        ).model_dump(mode="json"),
    }
    request = ModelRequest(model=model, messages=[], state=state)
    middleware = BackendRunMiddleware(_run_backend("token", invocation_id="run"))
    if retries:
        response = await middleware.awrap_model_call(request, handler)
        assert isinstance(response, ModelResponse)
        assert response.result[0].content == "fallback"
    else:
        with pytest.raises(type(error)) as caught:
            await middleware.awrap_model_call(request, handler)
        assert caught.value is error


@pytest.mark.parametrize("managed", [False, True])
async def test_backend_calls_carry_the_running_spans_trace_without_its_metadata(
    monkeypatch: pytest.MonkeyPatch,
    managed: bool,
) -> None:
    """Backend hooks retain their trace position without copying payloads or metadata."""
    from managed_deepagents._managed_middleware import wrap_middleware_with_managed_runtime
    from rt.run_tools import RunModelCallLimit

    monkeypatch.setenv("OPEN_SWE_BACKEND_URL", "https://backend.invalid")
    monkeypatch.setattr("rt.middleware.run_id", lambda: "run-1")
    request = httpx2.Request("POST", "https://backend.invalid")
    sent: dict[str, Mapping[str, str]] = {}

    async def post(
        url: str, *, headers: Mapping[str, str], json: dict[str, JsonValue], **kwargs: object
    ) -> httpx2.Response:
        if not url.endswith(REPORTS.path):
            sent[url.rsplit("/", 1)[-1]] = headers
            return httpx2.Response(200, request=request, json={})
        # Each report names its own span; the batch none.
        assert "langsmith-trace" not in headers
        items = Reports.model_validate(json).items
        for item in items:
            sent[item.endpoint] = {"Authorization": headers["Authorization"], **item.trace}
        taken = ReportsTaken(replies=[{} for _ in items])
        return httpx2.Response(200, request=request, json=taken.model_dump(mode="json"))

    client = MagicMock(is_closed=False)
    client.post = post
    monkeypatch.setattr("rt.backend.httpx2.AsyncClient", lambda **kwargs: client)
    monkeypatch.setattr(GenericFakeChatModel, "bind_tools", lambda self, *args, **kwargs: self)
    backend = _run_backend(invocation_id="turn")

    @tool
    async def look(text: str) -> str:
        """Look around."""
        return text

    # One list of differently-stated middleware, as the runtime assembles it.
    middleware = cast(
        "list[AgentMiddleware]",
        [
            BackendRunMiddleware(backend),
            RuntimeToolsMiddleware(backend, reported_elsewhere=()),
            RunModelCallLimit(backend, 10),
        ],
    )
    if managed:
        middleware = wrap_middleware_with_managed_runtime(middleware)
    graph = create_agent(
        model=GenericFakeChatModel(
            messages=iter(
                [
                    AIMessage(
                        content="",
                        tool_calls=[ToolCall(name="look", args={"text": "seen"}, id="call-1")],
                    ),
                    AIMessage(content="done"),
                ]
            )
        ),
        tools=[look],
        middleware=middleware,
    )
    traced = MagicMock(spec=Client)
    collector = RunCollectorCallbackHandler()
    with tracing_context(enabled=True):
        await graph.ainvoke(
            {"messages": [("user", "hello")]},
            {
                "callbacks": [LangChainTracer(client=traced, project_name="turns"), collector],
                "metadata": {"invocation_id": "turn", "secret_ish": "x" * 10_000},
            },
        )
    [turn] = [run for run in collector.traced_runs if run.name == "LangGraph"]
    assert {"before_model", "tool_started", "tool_result", "after_agent"} <= sent.keys()
    for hook, headers in sent.items():
        assert headers["Authorization"] == "Bearer secret-token", hook
        root, _, span = headers["langsmith-trace"].partition(".")
        assert root.endswith(str(turn.id)) and span, hook
        assert "langsmith-project=turns" in headers["baggage"], hook
        assert "metadata" not in headers["baggage"], hook
    created = [call.kwargs for call in traced.create_run.call_args_list]
    hooks = [
        run
        for run in created
        if str(run["name"]).startswith(
            ("BackendRunMiddleware.", "RuntimeToolsMiddleware.", "RunModelCallLimit.")
        )
    ]
    assert hooks
    assert all(run["inputs"] == {} for run in hooks)
    models = [run for run in created if run["run_type"] == "llm"]
    tools = [run for run in created if run["run_type"] == "tool" and run["name"] == "look"]
    assert models and tools
    assert all(run["inputs"] for run in [*models, *tools])


def test_the_backend_hears_where_summarization_cut_the_conversation() -> None:
    """Context the model no longer sees past the cut is sent again; the summary stays here."""
    state = {
        "open_swe_state": {"reply_surface": "slack"},
        "_summarization_event": {"cutoff_index": 4, "summary_message": HumanMessage("Earlier")},
    }

    assert held_state(state) == {"reply_surface": "slack", "visible_from": 4}
