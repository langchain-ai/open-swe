import asyncio
import contextvars
import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from contextlib import ExitStack, closing
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Final, Literal, Self, cast
from unittest.mock import AsyncMock, MagicMock
from urllib.error import URLError
from uuid import UUID

import anthropic
import httpx
import httpx2
import openai
import pytest
from deepagents import create_deep_agent
from deepagents.backends.local_shell import LocalShellBackend
from deepagents.backends.protocol import ExecuteResponse
from deepagents.middleware.filesystem import FilesystemMiddleware
from langchain.agents import create_agent
from langchain.agents.middleware import AgentMiddleware
from langchain.tools import ToolRuntime
from langchain_anthropic import ChatAnthropic
from langchain_core.callbacks import AsyncCallbackManagerForLLMRun, CallbackManagerForLLMRun
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
from langchain_core.tools import ToolException, tool
from langchain_core.tracers.langchain import LangChainTracer
from langchain_core.tracers.run_collector import RunCollectorCallbackHandler
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.errors import GraphInterrupt
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command
from langgraph_sdk.errors import (
    APIStatusError,
    InternalServerError,
    PermissionDeniedError,
    RateLimitError,
)
from langsmith import Client, agent_address, get_tracing_context, tracing_context
from langsmith.run_helpers import _set_tracing_context
from langsmith.run_trees import RunTree
from managed_deepagents import (
    ConnectionNotFoundError,
    CredentialAuthorizationRequiredError,
    CredentialStoreError,
)
from managed_deepagents._memory.layers import MemoryLayers
from managed_deepagents._memory_instructions import create_memory_instructions_middleware
from managed_deepagents.runtime import _parent_trace
from pydantic import BaseModel, JsonValue, SecretStr
from rt.backend import (
    BackendCallError,
    BackendUnavailableError,
    OpenSweBackend,
    Run,
    RunContext,
    held_state,
)
from rt.file_tools import FileToolsMiddleware
from rt.gateway import RUN_GATEWAY
from rt.git_evidence import TurnGit, turn_git
from rt.kinds import KINDS
from rt.loop import RunLoop
from rt.middleware import BackendRunMiddleware, RunState, RuntimeToolsMiddleware
from rt.model_calls import MODEL_OUTAGE_MESSAGE, model_call_guards
from rt.routing import Route, RouteClassifier
from rt.run_prompt import RunPromptMiddleware, ThreadPromptMiddleware
from rt.tools import STATE_KEY, called, operation_tool
from rt.tracing import RootAgentTraceMiddleware, TurnTrace
from runtime_api.catalog import (
    JOINED_TASK_CONTEXT,
    Catalog,
    FilesSpec,
    InboxSpec,
    LoopRules,
    MemoryScope,
    ModelSpec,
    RoutingRules,
    RunModels,
    SandboxCreate,
    SandboxSpec,
    TaskContext,
    ToolSpec,
)
from runtime_api.connections import ConnectionRef
from runtime_api.endpoint import Delivery, Endpoint, call
from runtime_api.loop import (
    GATEWAY_CREDENTIAL,
    MODEL_RESULT,
    PREPARE,
    Done,
    GatewayCredential,
    Inbox,
    InboxItems,
    InboxRead,
    InboxReceipt,
    ModelCall,
    ModelFailed,
    ModelResult,
    PhaseResult,
    Prepare,
    Prepared,
    ToolCallReport,
)
from runtime_api.operations import (
    OperationResult,
    PullRequestHead,
    ShellVerdict,
)
from runtime_api.reports import REPORTS, Reports, ReportsTaken
from runtime_api.workers import LAUNCH_WORKER


def _run_metadata(patch: Mapping[str, object]) -> Mapping[str, object]:
    """The metadata a LangSmith run update sets."""
    extra = patch.get("extra")
    metadata = extra.get("metadata") if isinstance(extra, Mapping) else None
    return metadata if isinstance(metadata, Mapping) else {}


type Answer = Callable[[str, dict[str, JsonValue]], Awaitable[BaseModel]]


# A run planned with nothing in particular.
_NO_PLAN: Final = Catalog()
_INBOX = InboxSpec(events=True, surface="slack")
# The thread's sandbox, which the backend names in every run's catalog.
_IN_SANDBOX: Final = SandboxSpec(
    create=SandboxCreate(fs_capacity_bytes=1, idle_ttl_seconds=60, delete_after_stop_seconds=60)
)


def _run_backend(
    token: str | None = "secret-token",
    *,
    worker_grant: str | None = None,
    invocation_id: str | None = None,
    catalog: Catalog = _NO_PLAN,
) -> OpenSweBackend:
    """The backend as a run of its own, outside any graph's run, started with ``catalog`` and
    the thread's sandbox, as the backend plans every run."""
    planned = (
        catalog
        if catalog.sandbox is not None
        else catalog.model_copy(update={"sandbox": _IN_SANDBOX})
    )
    context = RunContext.model_validate(
        {
            "__run_token": token,
            "__worker_grant": worker_grant,
            "invocation_id": invocation_id,
            "__catalog": planned.model_dump(mode="json"),
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


def _models(model: ModelSpec) -> Catalog:
    """A catalog that runs on ``model``."""
    return Catalog(models=RunModels(model=model, subagent_model=model))


def _preparing(**update: JsonValue) -> Answer:
    """A backend whose preparation writes ``update``, and that answers every other call."""

    async def answer(name: str, body: dict[str, JsonValue]) -> BaseModel:
        return Prepared(update=update) if name == "prepare" else PhaseResult()

    return answer


async def test_calls_to_the_backend_keep_their_connection_alive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A call held open past the firewall's idle limit still gets its reply (RBN-213)."""
    import socket

    from rt.backend import _Connections

    made: list[list[tuple[int, int, int]]] = []
    real = httpx2.AsyncHTTPTransport

    def transport(*, socket_options: list[tuple[int, int, int]]) -> httpx2.AsyncHTTPTransport:
        made.append(list(socket_options))
        return real(socket_options=socket_options)

    monkeypatch.setattr(httpx2, "AsyncHTTPTransport", transport)

    _Connections().client()

    [options] = made
    assert (socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1) in options
    assert all(seconds <= 60 for level, name, seconds in options if level == socket.IPPROTO_TCP)


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


@pytest.mark.parametrize("code_mode", [False, True])
@pytest.mark.parametrize(
    ("owner", "failure", "outcome"),
    [
        ("agent", None, {"open-swe-exa": "exa-key"}),
        ("user", CredentialStoreError("service callers cannot own OAuth grants"), {}),
        ("user", CredentialAuthorizationRequiredError(["open-swe-exa"]), {}),
        ("user", URLError(ConnectionRefusedError()), {}),
        ("user", GraphInterrupt(), "pauses"),
        ("agent", ConnectionNotFoundError("open-swe-exa", "agent"), "fails"),
        ("agent", URLError(ConnectionRefusedError()), "fails"),
    ],
)
async def test_an_operation_carries_what_the_run_reads_of_its_connections(
    monkeypatch: pytest.MonkeyPatch,
    owner: Literal["agent", "user"],
    failure: Exception | None,
    outcome: dict[str, str] | Literal["pauses", "fails"],
    code_mode: bool,
) -> None:
    from rt.backend import reporting, subagent_of

    monkeypatch.setenv("OPEN_SWE_BACKEND_URL", "https://backend.invalid")
    monkeypatch.setattr("rt.tools.run_id", lambda: "run-1")

    async def read() -> str:
        if failure is not None:
            raise failure
        return "exa-key"

    monkeypatch.setattr("rt.tools.connections", MagicMock(get=lambda slug, owner: read()))
    backend = _run_backend()
    calls: list[dict[str, JsonValue]] = []

    async def answer(_name: str, body: dict[str, JsonValue]) -> BaseModel:
        calls.append(body)
        return OperationResult(content="done")

    _answering(monkeypatch, backend, answer)
    started = MagicMock()
    finished = MagicMock()
    monkeypatch.setattr(backend.outbox, "started", started)
    monkeypatch.setattr(backend.outbox, "finished", finished)
    spec = ToolSpec(
        name="web_search",
        description="Search",
        parameters={"type": "object"},
        connections=[ConnectionRef(slug="open-swe-exa", owner=owner)],
    )
    runtime = ToolRuntime(
        state={"messages": []},
        context=None,
        config={},
        stream_writer=lambda event: None,
        tool_call_id=None if code_mode else "call-1",
        store=None,
    )
    served = operation_tool(spec, backend)
    assert served.coroutine is not None
    # Code mode reports the calls it makes itself, under the eval call that ran them.
    with ExitStack() as stack:
        if code_mode:
            stack.enter_context(subagent_of("eval-1"))
            stack.enter_context(reporting("eval-1"))
        if outcome == "pauses":
            with pytest.raises(GraphInterrupt):
                await served.coroutine(runtime=runtime)
        else:
            result = await served.coroutine(runtime=runtime)
    # A call that paused runs again once the person answers: it leaves no report open.
    assert started.call_count == finished.call_count == (code_mode and outcome != "pauses")
    if outcome == "pauses":
        assert calls == []
        return
    assert isinstance(result, ToolMessage)
    if outcome == "fails":
        assert result.status == "error"
        assert ("mda connections create open-swe-exa" in result.text) == (
            isinstance(failure, ConnectionNotFoundError)
        )
        assert calls == []
    else:
        assert result.status == "success"
        assert [body["credentials"] for body in calls] == [outcome]


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
@pytest.mark.parametrize("endpoint", [PREPARE, LAUNCH_WORKER])
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


@pytest.mark.parametrize("hook", ["prepare", "inbox"])
async def test_an_unreachable_backend_fails_the_run(
    monkeypatch: pytest.MonkeyPatch, hook: str
) -> None:
    monkeypatch.setenv("OPEN_SWE_BACKEND_URL", "https://backend.invalid")
    monkeypatch.setattr("rt.middleware.run_id", lambda: "run-1")
    backend = _run_backend(invocation_id="invocation-1", catalog=Catalog(inbox=_INBOX))

    async def phase(name: str, body: dict[str, JsonValue]) -> BaseModel:
        if name == hook:
            raise BackendUnavailableError(f"{hook} failed")
        return Inbox() if name == "inbox" else PhaseResult()

    _answering(monkeypatch, backend, phase)
    graph = create_agent(
        model=FakeListChatModel(responses=["done"]),
        middleware=[BackendRunMiddleware(backend)],
    )

    with pytest.raises(BackendUnavailableError):
        await graph.ainvoke({"messages": [("user", "hello")]})


@pytest.mark.parametrize("prepared_reads", [False, True])
async def test_the_inbox_names_only_the_run_and_hands_back_what_the_model_answers(
    monkeypatch: pytest.MonkeyPatch, prepared_reads: bool
) -> None:
    """A backend that reads the inbox as it prepares answers the first model call's read;
    one from before that is asked for it."""
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

    async def phase(name: str, body: dict[str, JsonValue]) -> BaseModel:
        if name == "prepare" and prepared_reads:
            asked = Prepare.model_validate(body).inbox
            assert asked is not None
            name, body = "inbox", asked.model_dump(mode="json")
        if name == "inbox":
            reads.append(InboxRead.model_validate(body))
            if len(reads) > 1:
                read_again.set()
            # What a receipt or the read itself names as received is owed no more.
            read = (
                Inbox()
                if received or reads[-1].received == items
                else Inbox.model_validate(
                    {
                        "messages": messages_to_dict([report]),
                        "update": {"reply_surface": "web"},
                        "items": items.model_dump(mode="json"),
                    }
                )
            )
            return Prepared(inbox=read) if len(reads) == 1 and prepared_reads else read
        if name == "inbox_received":
            await read_again.wait()
            received.append(InboxReceipt.model_validate(body))
            took.set()
        return PhaseResult()

    @tool
    async def look() -> str:
        """Look around."""
        return "seen"

    _answering(monkeypatch, backend, phase)
    monkeypatch.setattr(GenericFakeChatModel, "bind_tools", lambda self, *args, **kwargs: self)
    graph = create_agent(
        model=GenericFakeChatModel(
            messages=iter(
                [
                    AIMessage(content="", tool_calls=[ToolCall(name="look", args={}, id="c1")]),
                    AIMessage(content="second"),
                ]
            )
        ),
        tools=[look],
        middleware=[BackendRunMiddleware(backend)],
        checkpointer=InMemorySaver(),
    )
    config: RunnableConfig = {"configurable": {"thread_id": "inbox"}}

    result = await graph.ainvoke({"messages": [("user", "hello")]}, config)
    await asyncio.wait_for(took.wait(), 5)

    # The run and how its catalog reads the inbox; never its conversation or state.
    assert [(read.run_id, read.spec) for read in reads] == [("run-1", _INBOX)] * 2
    ids = [message.id for message in result["messages"]]
    # The first model call answers what the inbox brought.
    assert ids.index("event-match:report-1") == 1
    assert ids.count("event-match:report-1") == 1
    held = (await graph.aget_state(config)).values["open_swe_state"]
    assert held["reply_surface"] == "web"
    assert [receipt.items for receipt in received] == [items]
    # The next read names what the unsent receipt holds, so it cannot hand it back.
    assert reads[1].received == items


async def test_a_run_the_phase_ends_leaves_the_inbox_queued(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The loop ends the run before any call answers the follow-up: it stays queued."""
    monkeypatch.setenv("OPEN_SWE_BACKEND_URL", "https://backend.invalid")
    monkeypatch.setattr("rt.middleware.run_id", lambda: "run-1")
    backend = _run_backend(
        invocation_id="invocation-1",
        catalog=Catalog(inbox=_INBOX, loop=LoopRules(stop_when=["limit_reached"])),
    )
    follow_up = HumanMessage(content="One more thing", id="queued-1")
    called: list[str] = []

    async def phase(name: str, body: dict[str, JsonValue]) -> BaseModel:
        called.append(name)
        if name == "prepare":
            return Prepared(update={"limit_reached": True})
        if name == "inbox":
            return Inbox.model_validate(
                {"messages": messages_to_dict([follow_up]), "items": {"queued": [7]}}
            )
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
    backend = _run_backend(
        invocation_id="turn", catalog=Catalog(loop=LoopRules(stop_when=["scout_nothing_to_plan"]))
    )
    operations: list[str] = []

    async def phase(name: str, body: dict[str, JsonValue]) -> BaseModel:
        if name == "prepare":
            return Prepared(update={"scout_nothing_to_plan": False})
        if name == "operation":
            operations.append(str(body["name"]))
            assert operations == ["walkthrough_move_to_other"]
            return OperationResult(
                content='{"success": false, "error": "The pull request moved on. Stop."}',
                state={"scout_nothing_to_plan": True},
            )
        return PhaseResult()

    _answering(monkeypatch, backend, phase)
    tools = [
        operation_tool(
            ToolSpec(name=name, description=name, parameters={"type": "object"}), backend
        )
        for name in (
            "walkthrough_move_to_other",
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
                            ToolCall(name="walkthrough_move_to_other", args={}, id="call-1")
                        ],
                    )
                ]
            )
        ),
        tools=tools,
        middleware=[BackendRunMiddleware(backend)],
    )

    result = await graph.ainvoke({"messages": [("user", "Plan the walkthrough")]})

    assert operations == ["walkthrough_move_to_other"]
    assert isinstance(result["messages"][-1], ToolMessage)
    assert result["messages"][-1].tool_call_id == "call-1"


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
        "run_started",
        "model_result",
        "tool_started",
        "tool_result",
        "model_result",
        "run_ended",
        "model_result",
        "run_ended",
    ]
    assert {key for _, key in sent[:6]} == set(carried)
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


async def test_a_legacy_execution_notice_survives_checkpoint_replay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from rt.outbox import OUTBOX_KEY, Outbox

    monkeypatch.setenv("OPEN_SWE_BACKEND_URL", "https://backend.invalid")
    backend = _run_backend()
    taking = AsyncMock(return_value=ReportsTaken(replies=[{}]).model_dump(mode="json"))
    monkeypatch.setattr(backend, "post", taking)
    restored = Outbox(backend)
    restored.adopt(
        {
            OUTBOX_KEY: {
                "run-1:run_started": {
                    "order": 1,
                    "item": {
                        "key": "run-1:run_started",
                        "endpoint": "run_started",
                        "request": {
                            "run_id": "run-1",
                            "notices": [{"kind": "workspace_stale", "data": {"outdated": True}}],
                        },
                    },
                }
            }
        }
    )
    await restored.drain()

    taking.assert_awaited_once()
    [report] = Reports.model_validate(taking.call_args.args[1]).items
    assert report.request.model_dump(mode="json")["notices"] == [
        {"kind": "execution_stale", "data": {"outdated": True}}
    ]


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
    monkeypatch.setattr("rt.middleware.build_model", lambda spec: named)
    backend = _run_backend(
        invocation_id="invocation-1",
        catalog=_models(ModelSpec(model_id="anthropic:claude-opus-5-5", kwargs={})),
    )
    _answering(monkeypatch, backend, _preparing(rendered_system_prompt="You are Open SWE."))
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
    monkeypatch.setattr("rt.middleware.build_model", lambda spec: named)
    backend = _run_backend(
        invocation_id="invocation-1",
        catalog=_models(ModelSpec(model_id="anthropic:claude-opus-5-5", kwargs={})),
    )
    _answering(
        monkeypatch,
        backend,
        _preparing(
            rendered_system_prompt="You are Open SWE.\n\nThis thread is yours.",
            rendered_thread_prompt="This thread is yours.",
        ),
    )
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
    # As MDA mounts user memory: in deepagents' memory slot, inside the prompt cache.
    user_memory: MemoryLayers = [{"type": "user"}]
    graph = create_deep_agent(
        model=FakeListChatModel(responses=["unused"]),
        system_prompt="The graph's own instructions.",
        memory=["/memories/user/AGENTS.md"],
        middleware=[
            *middleware,
            create_memory_instructions_middleware(user_memory, lambda: "Prefers tabs."),
        ],
    )

    await graph.ainvoke({"messages": [HumanMessage("hi")]})

    [payload] = named.payloads
    hour = {"type": "ephemeral", "ttl": "1h"}
    system = cast(list[dict[str, object]], payload["system"])
    tools = cast(list[dict[str, object]], payload["tools"])
    # The graph's prompt, the run's, the thread's and the conversation's end: Anthropic
    # rejects a request with more than four breakpoints, so the memory after them has none.
    assert "Prefers tabs." in cast(str, system[-1]["text"])
    assert [block.get("cache_control") for block in system] == [hour, hour, hour, None]
    assert payload["cache_control"] == hour
    assert all("cache_control" not in tool for tool in tools)
    assert json.dumps(payload).count('"cache_control"') == 4
    assert '"5m"' not in json.dumps(payload)


class _PayloadOpenAI(ChatOpenAI):
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


@pytest.mark.parametrize(
    ("model", "marked"),
    [("gpt-6.1-sol", ["You are Open SWE.", "Robin-wide execution instructions."]), ("gpt-5.5", [])],
)
async def test_an_openai_model_caches_the_prompt_every_thread_shares(
    monkeypatch: pytest.MonkeyPatch, model: str, marked: list[str]
) -> None:
    """GPT-5.6 and later cache only up to a breakpoint, and the implicit one ends the
    request, which no other thread's request starts with: the shared prompt and the
    execution instructions each end in an explicit one. Earlier models reject the field."""
    monkeypatch.setenv("OPEN_SWE_BACKEND_URL", "https://backend.invalid")
    monkeypatch.setattr("rt.middleware.run_id", lambda: "run-1")
    named = _PayloadOpenAI(
        model=model, api_key=SecretStr("test-key"), use_responses_api=True, store=False, payloads=[]
    )
    monkeypatch.setattr("rt.middleware.build_model", lambda spec: named)
    backend = _run_backend(
        invocation_id="invocation-1",
        catalog=_models(ModelSpec(model_id=f"openai:{model}", kwargs={})),
    )
    _answering(
        monkeypatch,
        backend,
        _preparing(
            rendered_system_prompt="You are Open SWE.\n\nRobin-wide execution instructions.\n\nThis thread is yours.",
            rendered_execution_prompt="Robin-wide execution instructions.",
            rendered_thread_prompt="This thread is yours.",
        ),
    )
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
    [system, *_] = cast(list[dict[str, object]], payload["input"])
    blocks = cast(list[dict[str, object]], system["content"])
    assert system["role"] == "system"
    assert [block["text"] for block in blocks if "prompt_cache_breakpoint" in block] == marked
    assert json.dumps(payload).count('"prompt_cache_breakpoint": {"mode": "explicit"}') == len(
        marked
    )
    assert "This thread is yours." in cast(str, blocks[-1]["text"])
    # The implicit breakpoint still ends the request, for the thread's next call.
    assert "prompt_cache_options" not in payload
    assert '"cache_control"' not in json.dumps(payload)


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
    monkeypatch.setattr("rt.middleware.build_model", lambda spec: built[spec.model_id])
    model = ModelSpec(
        model_id="anthropic:claude-opus-5-5",
        kwargs={},
        fallback=ModelSpec(model_id="anthropic:claude-sonnet-5-5", kwargs={}),
    )
    backend = _run_backend(invocation_id="invocation-1", catalog=_models(model))
    _answering(monkeypatch, backend, _preparing(rendered_system_prompt="You are Open SWE."))
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


async def test_a_run_whose_login_lapsed_answers_why_even_without_a_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The check runs on every planned call, not only on one a fallback guards: else the
    request's own auth fails as a connection error and the run ends as an outage. The
    answer reaches the backend's transcript, where a dashboard thread shows it."""
    monkeypatch.setenv("OPEN_SWE_BACKEND_URL", "https://backend.invalid")
    monkeypatch.setattr("rt.middleware.run_id", lambda: "run-1")
    model = _RecordingAnthropic(model_name="claude-opus-5-5", api_key="test-key", sent=[])
    monkeypatch.setattr("rt.middleware.build_model", lambda spec: model)
    backend = _run_backend(
        invocation_id="invocation-1",
        catalog=_models(ModelSpec(model_id="anthropic:claude-opus-5-5", kwargs={})),
    )
    preparing = _preparing(rendered_system_prompt="You are Open SWE.")
    replies: list[dict[str, JsonValue]] = []
    asked: list[str] = []

    async def answer(name: str, body: dict[str, JsonValue]) -> BaseModel:
        asked.append(name)
        if name == GATEWAY_CREDENTIAL.name:
            return GatewayCredential(unavailable="Sign in to Robin again.")
        if name == MODEL_RESULT.name:
            replies.append(body)
        return await preparing(name, body)

    _answering(monkeypatch, backend, answer)
    monkeypatch.setattr("rt.gateway.current_run", lambda: backend.run)
    middleware = cast(
        list[AgentMiddleware],
        [
            BackendRunMiddleware(backend, read_ahead=[RUN_GATEWAY.read_ahead]),
            *model_call_guards(timeout_seconds=1.0),
        ],
    )
    graph = create_agent(model=FakeListChatModel(responses=["unused"]), middleware=middleware)

    result = await graph.ainvoke({"messages": [HumanMessage("hi")]})

    assert result["messages"][-1].content == "Sign in to Robin again."
    assert model.sent == []
    # Asked for once, while the run prepares: the model call's check waits on that read.
    assert asked.count(GATEWAY_CREDENTIAL.name) == 1
    reported = [ModelResult.model_validate(body).response for body in replies]
    # The transcript records a reply under its id.
    assert [(m.content, bool(m.id)) for r in reported for m in messages_from_dict(r)] == [
        ("Sign in to Robin again.", True)
    ]


@pytest.mark.parametrize("selection", ["primary", "route", "vision", "fallback"])
async def test_turn_model_tracks_the_selected_call_without_relabeling_llm_spans(
    monkeypatch: pytest.MonkeyPatch, selection: str
) -> None:
    monkeypatch.setenv("OPEN_SWE_BACKEND_URL", "https://backend.invalid")
    monkeypatch.setattr("rt.middleware.run_id", lambda: "run-1")
    vision = ModelSpec(model_id="anthropic:vision", kwargs={})
    fallback = ModelSpec(model_id="anthropic:fallback", kwargs={})
    primary = ModelSpec(model_id="anthropic:primary", kwargs={}, reads_images=selection != "vision")
    routed = ModelSpec(
        model_id="anthropic:routed",
        kwargs={},
        fallback=fallback if selection == "fallback" else None,
    )
    built: dict[str, ChatAnthropic] = {
        spec.model_id: _RecordingAnthropic(model_name=spec.model_id, api_key="test", sent=[])
        for spec in (primary, routed, vision, fallback)
    }
    if selection == "fallback":
        built[routed.model_id] = _StalledAnthropic(model_name=routed.model_id, api_key="test")
    monkeypatch.setattr("rt.middleware.build_model", lambda spec: built[spec.model_id])
    backend = _run_backend(
        invocation_id="turn",
        catalog=Catalog(
            models=RunModels(
                model=primary,
                subagent_model=primary,
                routes={"fast": routed},
                vision=vision,
            )
        ),
    )
    routing = {"model_route": "fast"} if selection in {"route", "fallback"} else {}
    _answering(monkeypatch, backend, _preparing(**routing))
    client = MagicMock(spec=Client)
    patches: dict[object, dict[str, object]] = {}

    def update(run_id: object, **kwargs: object) -> None:
        patches[run_id] = deepcopy(kwargs)

    client.update_run.side_effect = update
    graph = create_agent(
        model=FakeListChatModel(responses=["unused"]),
        middleware=cast(
            list[AgentMiddleware],
            [BackendRunMiddleware(backend), *model_call_guards(timeout_seconds=1.0)],
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


class _AnsweringThenDown(GenericFakeChatModel):
    """A model that answers with its messages, then fails every later call."""

    def bind_tools(self, *_args: object, **_kwargs: object) -> Self:
        return self

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: object,
    ) -> ChatResult:
        try:
            return super()._generate(messages, stop, run_manager, **kwargs)
        except StopIteration:
            raise RuntimeError("the model is down") from None


_TOOL_COUNTS: Final = ("tool_calls", "failed_tool_calls", "nonzero_shell_exits")


class _Shell(LocalShellBackend):
    """A shell where ``make test`` exits 2, a ``grep`` that matches nothing exits 1, and
    ``wait`` answers with no exit code; every other command exits 0."""

    EXITS: Final = {"make test": 2, "grep -r TODO .": 1, "wait": None}

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        return ExecuteResponse(output=command, exit_code=self.EXITS.get(command, 0))


@pytest.mark.parametrize(
    ("arm", "model_id"),
    [("active", "openai:sol"), ("inactive", "anthropic:claude-opus-5-5")],
)
@pytest.mark.parametrize("fails", [False, True], ids=["ends", "fails"])
async def test_the_turn_root_counts_its_tool_calls_and_their_errors_on_either_arm(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, arm: str, model_id: str, fails: bool
) -> None:
    monkeypatch.setenv("OPEN_SWE_BACKEND_URL", "https://backend.invalid")
    monkeypatch.setattr("rt.middleware.run_id", lambda: "run-1")
    spec = ModelSpec(model_id=model_id, kwargs={})
    backend = _run_backend(
        invocation_id="turn",
        catalog=_models(spec).model_copy(
            update={
                "trace_metadata": {"model_routing": arm},
                "trace_tags": [f"model_routing:{arm}"],
            }
        ),
    )
    _answering(monkeypatch, backend, _preparing())
    calls = [
        ToolCall(name="look", args={"path": "a"}, id="call-1"),
        ToolCall(name="look", args={"path": "missing"}, id="call-2"),
        ToolCall(name="look", args={"path": "b"}, id="call-3"),
        ToolCall(name="execute", args={"command": "make test"}, id="call-4"),
        ToolCall(name="execute", args={"command": "make lint"}, id="call-5"),
        ToolCall(name="execute", args={"command": "grep -r TODO ."}, id="call-6"),
        ToolCall(name="execute", args={"command": "wait"}, id="call-7"),
    ]
    replies = [AIMessage(content="", tool_calls=calls)]
    if not fails:
        replies.append(AIMessage(content="done"))
    model = _AnsweringThenDown(messages=iter(replies))
    monkeypatch.setattr("rt.middleware.build_model", lambda _spec: model)

    @tool
    def look(path: str) -> str:
        """Read a path."""
        if path == "missing":
            raise ToolException("no such path")
        return path

    # As the tools node answers a tool's failure: an error result the model reads.
    look.handle_tool_error = True
    # deepagents' own shell tool, which answers a command that exits non-zero with success.
    execute = next(
        shell_tool
        for shell_tool in FilesystemMiddleware(backend=_Shell(root_dir=tmp_path)).tools
        if shell_tool.name == "execute"
    )

    client = MagicMock(spec=Client)
    patches: dict[object, dict[str, object]] = {}
    client.update_run.side_effect = lambda run_id, **kwargs: patches.update(
        {run_id: deepcopy(kwargs)}
    )
    monkeypatch.setattr(FakeListChatModel, "bind_tools", lambda self, *args, **kwargs: self)
    graph = create_agent(
        model=FakeListChatModel(responses=["unused"]),
        tools=[look, execute],
        middleware=cast(
            list[AgentMiddleware],
            [BackendRunMiddleware(backend), RuntimeToolsMiddleware(backend, ())],
        ),
    )
    with tracing_context(enabled=True):
        ran = graph.ainvoke(
            {"messages": [HumanMessage("look around")]},
            {
                "callbacks": [LangChainTracer(client=client, project_name="test")],
                "metadata": {"invocation_id": "turn"},
            },
        )
        if fails:
            with pytest.raises(RuntimeError, match="the model is down"):
                await ran
        else:
            await ran
    await backend.run.trace.ended()

    created = {call.kwargs["id"]: call.kwargs for call in client.create_run.call_args_list}
    root_id = next(run_id for run_id, run in created.items() if not run.get("parent_run_id"))
    root = _run_metadata(patches[root_id])
    # The same fields on both arms of the routing experiment, a failed run's included. The
    # tool's error fails its call alone; each non-zero exit fails its call and counts as one.
    assert {key: root[key] for key in (*_TOOL_COUNTS, "model")} == {
        "tool_calls": 7,
        "failed_tool_calls": 3,
        "nonzero_shell_exits": 2,
        "model": model_id,
    }
    children = [patch for run_id, patch in patches.items() if run_id != root_id]
    assert children
    assert not any(key in _run_metadata(patch) for patch in children for key in _TOOL_COUNTS)


@pytest.mark.parametrize(
    ("raised", "counts"),
    [(None, (1, 1, 0)), (RuntimeError, (1, 1, 0)), (GraphInterrupt, (0, 0, 0))],
    ids=["errs", "raises", "pauses"],
)
async def test_a_code_mode_call_paused_for_the_person_is_not_counted(
    monkeypatch: pytest.MonkeyPatch,
    raised: type[Exception] | None,
    counts: tuple[int, int, int],
) -> None:
    from rt.backend import reporting, subagent_of

    monkeypatch.setenv("OPEN_SWE_BACKEND_URL", "https://backend.invalid")
    monkeypatch.setattr("rt.tools.run_id", lambda: "run-1")
    backend = _run_backend()
    started = MagicMock()
    finished = MagicMock()
    monkeypatch.setattr(backend.outbox, "started", started)
    monkeypatch.setattr(backend.outbox, "finished", finished)
    root = RunTree(name="turn", run_type="chain")
    backend.run.trace.root = root

    async def make(tool_call_id: str) -> ToolMessage:
        if raised is not None:
            # As an MCP tool fails, or asks the person to grant its connection.
            raise raised
        return ToolMessage(content="no such page", tool_call_id=tool_call_id, status="error")

    runtime = ToolRuntime(
        state={"messages": []},
        context=None,
        config={},
        stream_writer=lambda event: None,
        tool_call_id=None,
        store=None,
    )
    with subagent_of("eval-1"), reporting("eval-1"):
        if raised is None:
            await called(backend, "fetch_page", {}, runtime, make)
        else:
            with pytest.raises(raised):
                await called(backend, "fetch_page", {}, runtime, make)

    assert tuple(root.metadata.get(key, 0) for key in _TOOL_COUNTS) == counts
    # Code mode runs a paused call again under a new id, so this one's report still closes.
    assert started.call_count == finished.call_count == 1


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
    ("name", "arguments", "checked"),
    [
        ("execute", {"command": "cd /workspace/repo && git push origin HEAD"}, True),
        ("execute", {"command": "bash -c 'gh pr create --fill'"}, True),
        ("execute", {"command": "curl -X POST https://api.github.com/repos/o/r/pulls"}, True),
        ("execute", {"command": 'g"h" pr create --fill'}, True),
        ("execute", {"command": "git pu''sh origin HEAD"}, True),
        ("execute", {"command": "git pu\\\nsh origin HEAD"}, True),
        ("execute", {"command": "uv run pytest -q tests/unit"}, False),
        ("read_file", {"file_path": "/workspace/repo/README.md"}, False),
    ],
)
async def test_sandbox_tools_ask_the_guards_first(
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    arguments: dict[str, str],
    checked: bool,
) -> None:
    """A command the push guards may stop is put to them first, then runs in the sandbox."""
    monkeypatch.setenv("OPEN_SWE_BACKEND_URL", "https://backend.invalid")
    for module in ("rt.middleware", "rt.tools", "rt.file_tools"):
        monkeypatch.setattr(f"{module}.run_id", lambda: "run-1")
    backend = _run_backend(
        catalog=Catalog(
            files=FilesSpec(guards=["shell_commands"]),
            sandbox=_IN_SANDBOX,
        )
    )
    checks = AsyncMock(return_value=ShellVerdict(allowed=True).model_dump(mode="json"))
    monkeypatch.setattr(backend, "post", checks)
    monkeypatch.setattr(backend.outbox, "started", MagicMock())
    monkeypatch.setattr(backend.outbox, "finished", MagicMock())
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
    in_sandbox = AsyncMock(return_value=ToolMessage(content="ran", tool_call_id="call-1"))
    files = FileToolsMiddleware(backend, 300)

    await files.awrap_tool_call(request, in_sandbox)
    await backend.outbox.drain()

    assert checks.await_count == int(checked)
    in_sandbox.assert_awaited_once()


@pytest.mark.parametrize("classified_ahead", [True, False])
@pytest.mark.parametrize("launched_worker", [False, True])
@pytest.mark.parametrize("next_trace_available", [True, False])
async def test_turn_reconciliation_reaches_runtime_root_llm_and_nested_calls(
    monkeypatch: pytest.MonkeyPatch,
    next_trace_available: bool,
    launched_worker: bool,
    classified_ahead: bool,
) -> None:
    monkeypatch.setenv("OPEN_SWE_BACKEND_URL", "https://backend.invalid")
    monkeypatch.setattr("rt.middleware.run_id", lambda: "run-1")
    monkeypatch.setenv("LANGSMITH_REVISION_ID", "runtime-sha")
    monkeypatch.setenv("MDA_DEPLOYMENT_NAME", "open-swe-mda-prod")
    actor = {
        "ls_repo_provider": "github",
        "ls_attribution_identifier": "trigger",
        "LANGSMITH_AGENT_VERSION": "backend-sha",
        "user_id": "7e6f62e6-0313-413c-b964-710ddc5461a4",
        "billed_user_email": "human@example.com",
        "billing_bucket": "user",
        "ls_git_repo": "org/configured",
    }
    final = {**actor, "ls_git_repo": "org/actual", "ls_git_branch": "Exact/PR-head"}
    backend = _run_backend(
        invocation_id="turn",
        catalog=Catalog(
            trace_metadata=actor,
            trace_tags=["model_routing:active"],
            inbox=_INBOX,
            loop=LoopRules(routing=RoutingRules(shadow=True)),
        ),
    )
    git = turn_git(backend.run)

    async def classify(_self: RouteClassifier, _messages: Sequence[object]) -> Route:
        await FakeListChatModel(responses=["lookup"]).ainvoke("classify")
        return Route("default", answers={"task_type": "lookup"})

    monkeypatch.setattr(RouteClassifier, "classify", classify)
    if not classified_ahead:
        monkeypatch.setattr(RunLoop, "classify_ahead", lambda self, state: None)

    @tool
    async def nested_call(text: str) -> str:
        """Call a nested runnable."""
        # As the run's operations report the pull requests they open, earliest first.
        git.found_pull_requests([PullRequestHead(repo="org/later", branch="later/head")], 2)
        git.found_pull_requests(
            [PullRequestHead(repo="org/actual", branch=final["ls_git_branch"])], 1
        )
        return await RunnableLambda(lambda value: value).ainvoke(text)

    async def phase(name: str, body: dict[str, JsonValue]) -> BaseModel:
        if name == "inbox":
            await nested_call.ainvoke({"text": "nested"})
            return Inbox()
        return PhaseResult()

    _answering(monkeypatch, backend, phase)
    client = MagicMock(spec=Client)
    updates: list[tuple[object, dict[str, object]]] = []

    def update(run_id: object, **kwargs: object) -> None:
        updates.append((run_id, deepcopy(kwargs)))

    client.update_run.side_effect = update
    tracer = LangChainTracer(client=client, project_name="test")
    parent = RunTree(
        name="external request",
        run_type="chain",
        ls_client=client,
        extra={"metadata": {"created_by": "system"}},
    )
    monkeypatch.setattr(GenericFakeChatModel, "bind_tools", lambda self, *args, **kwargs: self)

    @tool
    async def look() -> str:
        """Look around."""
        return "seen"

    graph = create_deep_agent(
        tools=[look],
        model=GenericFakeChatModel(
            messages=iter(
                [
                    AIMessage(content="", tool_calls=[ToolCall(name="look", args={}, id="c1")]),
                    AIMessage(content="done"),
                    AIMessage(content="done again"),
                    AIMessage(content="done on the branch"),
                ]
            )
        ),
        middleware=[BackendRunMiddleware(backend)],
        checkpointer=InMemorySaver(),
    )
    config: RunnableConfig = {
        "configurable": {"thread_id": "trace-test"},
        "callbacks": [tracer],
        "metadata": {
            # deepagents launches a worker's run with its own thread and no invocation.
            **({"thread_id": "worker-thread"} if launched_worker else {"invocation_id": "turn"}),
            "created_by": "system",
            "existing_key": "preserved",
            "revision_id": "queued-sha",
            "LANGSMITH_AGENT_VERSION": "queued-agent-sha",
            "environment": "queued-environment",
        },
    }
    with tracing_context(enabled=True, parent=parent):
        await graph.ainvoke(
            {"messages": [("user", "hello")], "open_swe_state": {"kept": True}}, config
        )
    await backend.run.trace.ended()
    saved = await graph.aget_state(config)
    # A turn starts with no task joined since its catalog was planned.
    assert saved.values["open_swe_state"] == {
        "kept": True,
        JOINED_TASK_CONTEXT: None,
        "jev_shadowed": True,
        "model_route": "default",
    }
    assert "ls_git_repo" not in parent.metadata
    created = {call.kwargs["id"]: call.kwargs for call in client.create_run.call_args_list}
    assert any(run["run_type"] == "llm" for run in created.values())
    assert any(run["run_type"] == "tool" for run in created.values())
    sent_git: dict[str, set[tuple[object, object]]] = {"llm": set(), "tool": set()}
    for run in created.values():
        if run["run_type"] == "llm" or run["name"] == "look":
            sent = _run_metadata(run)
            assert {key for key, value in actor.items() if sent.get(key) != value} <= {
                "LANGSMITH_AGENT_VERSION",
                "ls_git_repo",
            }
            assert (sent["ls_harness_name"], sent["ls_agent_runtime"], sent["ls_agent_type"]) == (
                "robin",
                "robin",
                "root",
            )
            sent_git[run["run_type"]].add((sent.get("ls_git_repo"), sent.get("ls_git_branch")))
    assert sent_git == {
        "llm": {("org/configured", None), ("org/actual", "Exact/PR-head")},
        "tool": {("org/actual", "Exact/PR-head")},
    }
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
    assert "created_by" not in reconciled[root_id]
    # The thread's first run, which charts count its threads by.
    assert reconciled[root_id]["thread_start"] is True
    assert parent.metadata["created_by"] == "system"
    root_tags = next(patch["tags"] for run_id, patch in updates if run_id == root_id)
    assert isinstance(root_tags, list)
    assert "model_routing:active" in root_tags
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
    await backend.run.trace.ended()
    assert all(run_id not in created for run_id, _ in updates[previous_update_count:])
    # A later run on the thread doesn't start it again.
    assert not any(
        _run_metadata(patch).get("thread_start") for _, patch in updates[previous_update_count:]
    )
    if next_trace_available:
        # A Slack branch is a new thread seeded with its source's state, prepared marks
        # included; its first run starts it.
        source = (await graph.aget_state(config)).values
        branch_update_count = len(updates)
        with tracing_context(enabled=True, parent=parent):
            await graph.ainvoke(
                {
                    "messages": [("user", "branch")],
                    "open_swe_prepared_for": source["open_swe_prepared_for"],
                    "open_swe_thread": source["open_swe_thread"],
                },
                {
                    **config,
                    "configurable": {"thread_id": "branch"},
                    "metadata": {"invocation_id": "branch-turn"},
                },
            )
        await backend.run.trace.ended()
        assert any(
            _run_metadata(patch).get("thread_start") for _, patch in updates[branch_update_count:]
        )


@pytest.mark.parametrize("creator", ["system", "known-creator", None])
async def test_root_attribution_survives_a_missing_catalog(
    monkeypatch: pytest.MonkeyPatch, creator: str | None
) -> None:
    monkeypatch.setenv("OPEN_SWE_BACKEND_URL", "https://backend.invalid")
    backend = RunContext(run_token="token", invocation_id="turn").run().backend
    client = MagicMock(spec=Client)
    updates: list[tuple[object, dict[str, object]]] = []

    def update(run_id: object, **kwargs: object) -> None:
        updates.append((run_id, deepcopy(kwargs)))

    client.update_run.side_effect = update
    graph = create_agent(
        model=FakeListChatModel(responses=["unused"]),
        middleware=[BackendRunMiddleware(backend)],
    )
    billing = {
        "user_id": "7e6f62e6-0313-413c-b964-710ddc5461a4",
        "billing_bucket": "user",
    }
    with tracing_context(enabled=True):
        with pytest.raises(BackendCallError, match="without its catalog"):
            await graph.ainvoke(
                {"messages": [("user", "hello")]},
                {
                    "callbacks": [LangChainTracer(client=client, project_name="test")],
                    "metadata": {"invocation_id": "turn", "created_by": creator, **billing},
                },
            )
    root = backend.run.trace.root
    assert root is not None
    assert root.metadata.items() >= billing.items()
    assert (root.metadata["ls_harness_name"], root.metadata["ls_agent_type"]) == ("robin", "root")
    assert root.metadata.get("created_by") == (None if creator == "system" else creator)
    root_patches = [patch for run_id, patch in updates if run_id == root.id]
    assert root_patches
    assert _run_metadata(root_patches[-1]).items() >= billing.items()
    assert _run_metadata(root_patches[-1]).get("created_by") != "system"


def test_labeled_calls_keep_where_the_turn_traces_to() -> None:
    client = MagicMock(spec=Client)
    root = RunTree(name="turn", run_type="chain", ls_client=client)
    with tracing_context(
        enabled=True,
        parent=root,
        project_name="robin-prod",
        client=client,
        tags=["turn"],
        metadata={"kept": True, "ls_agent_type": "subagent"},
    ):
        before = get_tracing_context()
        with TurnTrace.labeled({"ls_git_repo": "org/repo"}):
            inside = get_tracing_context()

    assert {key: inside[key] for key in before if key != "metadata"} == {
        key: value for key, value in before.items() if key != "metadata"
    }
    assert inside["metadata"] == {
        "ls_harness_name": "robin",
        "ls_agent_runtime": "robin",
        "ls_agent_type": "subagent",
        "kept": True,
        "ls_git_repo": "org/repo",
    }


def test_labeled_calls_skip_their_parent_steps_own_tags() -> None:
    client = MagicMock(spec=Client)
    step = RunTree(
        name="step",
        run_type="chain",
        ls_client=client,
        tags=["graph:step:4"],
        extra={"metadata": {"step_only": True}},
    )

    def labeled_in_a_graph_step() -> tuple[dict[str, object], RunTree]:
        _set_tracing_context({"parent": step, "enabled": True, "client": client})
        with TurnTrace.labeled({"ls_git_repo": "org/repo"}):
            return get_tracing_context(), step.create_child(name="call", run_type="llm")

    inside, call = contextvars.copy_context().run(labeled_in_a_graph_step)
    tags, metadata = inside["tags"], inside["metadata"]
    assert tags is None or isinstance(tags, list)
    assert isinstance(metadata, dict)

    assert inside["parent"] is step
    assert "graph:step:4" not in (tags or [])
    assert "step_only" not in metadata
    assert "step_only" not in call.metadata
    assert metadata["ls_git_repo"] == "org/repo"


async def test_a_resumed_runs_calls_are_labeled_without_its_root() -> None:
    client = MagicMock(spec=Client)
    trace = TurnTrace()

    async def resumed(_: object) -> None:
        with trace.labeled({"ls_git_repo": "org/repo"}):
            await FakeListChatModel(responses=["done"]).ainvoke("hello")

    await RunnableLambda(resumed).ainvoke(
        None, {"callbacks": [LangChainTracer(client=client, project_name="test")]}
    )

    [llm] = [
        call.kwargs for call in client.create_run.call_args_list if call.kwargs["run_type"] == "llm"
    ]
    assert trace.root is None
    assert _run_metadata(llm)["ls_git_repo"] == "org/repo"
    assert _run_metadata(llm)["ls_harness_name"] == "robin"


async def test_a_run_planned_without_a_sandbox_ends_before_it_prepares(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A backend from before every run had a sandbox never gets a run that works without one."""
    monkeypatch.setenv("OPEN_SWE_BACKEND_URL", "https://backend.invalid")
    context = {"__run_token": "token", "__catalog": Catalog().model_dump(mode="json")}
    backend = Run(RunContext.model_validate(context)).backend
    post = AsyncMock()
    monkeypatch.setattr(backend, "post", post)
    graph = create_agent(
        model=FakeListChatModel(responses=["unused"]),
        middleware=[BackendRunMiddleware(backend)],
    )

    with pytest.raises(BackendCallError, match="This run has no sandbox"):
        await graph.ainvoke({"messages": [("user", "hello")]})

    post.assert_not_awaited()


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
        labeled = root.create_child(
            name="labeled model",
            run_type="llm",
            start_time=started,
            extra={
                "metadata": {
                    "invocation_id": "turn",
                    "ls_git_repo": "org/repo",
                    "ls_git_branch": "feature",
                    "ls_harness_name": "robin",
                    "ls_agent_runtime": "robin",
                    "ls_agent_type": "subagent",
                }
            },
        )
        labeled.end(outputs={"answer": "done"}, end_time=ended)
        trace = TurnTrace()
        trace.root = root

        async def git() -> dict[str, JsonValue]:
            return {"ls_git_branch": "feature"}

        trace.end({"ls_git_repo": "org/repo"}, git)
        await trace.ended()
    child_patch = next(patch for patch in patches if patch["id"] == child.id)
    for patch in patches:
        assert patch["end_time"] == ended.isoformat()
        assert "inputs" not in patch
        assert patch["outputs"] == {"answer": "done"}
        assert isinstance(extra := patch["extra"], dict)
        assert extra["metadata"]["ls_git_repo"] == "org/repo"
        assert extra["metadata"]["ls_git_branch"] == "feature"
        assert extra["metadata"]["ls_harness_name"] == "robin"
    assert len(patches) == 1
    if destination == "project":
        assert child_patch["session_name"] == "source"
    else:
        assert child_patch["address"] == "lrn:agents/open-swe/environments/production"
    assert child.outputs == {"answer": "done"}
    assert root.end_time is None
    assert root.metadata["ls_git_repo"] == child.metadata["ls_git_repo"] == "org/repo"
    assert labeled.metadata["ls_agent_type"] == "subagent"


@pytest.mark.parametrize("failure", ["lookup", "export", "fingerprint", "label"])
async def test_optional_trace_failures_do_not_change_successful_turn_output(
    monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    monkeypatch.setenv("OPEN_SWE_BACKEND_URL", "https://backend.invalid")
    monkeypatch.setattr("rt.middleware.run_id", lambda: "run-1")
    backend = _run_backend(invocation_id="turn", catalog=Catalog(inbox=_INBOX))

    def unavailable_trace() -> None:
        raise RuntimeError("trace lookup unavailable")

    async def stuck_fingerprint(self: TurnGit) -> dict[str, JsonValue]:
        await asyncio.Event().wait()
        return {}

    if failure == "lookup":
        monkeypatch.setattr(TurnTrace, "current_root", staticmethod(unavailable_trace))
    if failure == "fingerprint":
        # The run's end never waits for the turn's last fingerprint.
        monkeypatch.setattr(TurnGit, "metadata", stuck_fingerprint)
    if failure == "label":
        monkeypatch.setattr("rt.tracing.get_tracing_context", unavailable_trace)

    git = turn_git(backend.run)

    async def phase(name: str, body: dict[str, JsonValue]) -> BaseModel:
        if name == "inbox":
            git.found_pull_requests([PullRequestHead(repo="org/actual", branch="main")], 1)
            return Inbox()
        return PhaseResult()

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
        result = await asyncio.wait_for(
            graph.ainvoke(
                {"messages": [("user", "hello")]},
                {
                    "callbacks": [LangChainTracer(client=client, project_name="test")],
                    "metadata": {"invocation_id": "turn"},
                },
            ),
            5,
        )
    assert result["messages"][-1].content == "done"
    assert export_failed == (failure == "export")


async def test_an_operation_code_mode_calls_is_reported_under_its_eval_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Code mode calls tools past the tool-call chain, so the tool reports itself."""
    from rt.backend import reporting, subagent_of

    monkeypatch.setenv("OPEN_SWE_BACKEND_URL", "https://backend.invalid")
    monkeypatch.setattr("rt.tools.run_id", lambda: "run-1")
    backend = _run_backend()
    answers = {
        "/agent-api/operation": OperationResult(content="found it"),
    }

    async def post(path: str, body: dict[str, object], **kwargs: object) -> object:
        return answers[path].model_dump(mode="json")

    monkeypatch.setattr(backend, "post", post)
    started = MagicMock()
    finished = MagicMock()
    monkeypatch.setattr(backend.outbox, "started", started)
    monkeypatch.setattr(backend.outbox, "finished", finished)
    search = operation_tool(
        ToolSpec(name="search_threads", description="Search", parameters={"type": "object"}),
        backend,
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
    assert result.content == "found it"
    [[report]] = [call.args for call in started.call_args_list]
    assert report.name == "search_threads"
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
        catalog=Catalog(files=FilesSpec(guards=["shell_commands"]), sandbox=_IN_SANDBOX)
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
    in_sandbox = AsyncMock()
    files = FileToolsMiddleware(backend, 300)

    refused = await files.awrap_tool_call(request, in_sandbox)
    await backend.outbox.drain()

    assert isinstance(refused, ToolMessage) and refused.status == "error"
    in_sandbox.assert_not_awaited()
    started.assert_called_once()
    [[_body, recorded]] = [call.args for call in finished.call_args_list]
    assert recorded.content == "pr_creation_fallback_blocked"


@pytest.mark.parametrize(
    ("command", "timeout"),
    [
        ("make build", 1500),
        ("make build", 1500.0),
        ("make build", 0),
        ("make build", 0.0),
        ("sleep 295; cat /tmp/open-swe-background-tasks/cmd-1/output.log", 300),
        ("sleep 5m; cat /tmp/open-swe-background-tasks/cmd-1/output.log", 360),
        ("sleep 295 >/dev/null 2>&1; cat output.log", 300),
        ("sleep 295 | cat; cat output.log", 300),
        ("sleep 295 # wait for output", 300),
        ("sleep 60.5; cat output.log", 300),
        ("sleep 30 30; cat output.log", 300),
        ('sleep 60 >"&"; cat output.log', 300),
        ("sleep 60 # wait\necho ready &", 300),
        ("sleep 295 && (make test & wait)", 300),
        (r"sleep 295 | echo \&", 300),
        ("sleep 120 | (cat & wait)", 300),
        ("sleep 120 && printf $'ready; done\\n' &", 300),
        ("sleep 120 && printf $'ready & waiting\\n'", 300),
        ("sleep 120 >$'ready & waiting'", 300),
        ('sleep 120 >"run.log" &', 300),
        ("sleep 120 >'unfinished &", 300),
        ("cd /tmp/tri && sleep 60; cat output.log", 300),
        (
            "timeout 290 bash -c 'until grep -q EXIT "
            "/tmp/open-swe-background-tasks/cmd-1/output.log; do sleep 5; done'",
            300,
        ),
        (
            "for i in $(seq 1 110); do rg -q done "
            "/tmp/open-swe-background-tasks/cmd-2/output.log && break; sleep 5; done",
            600,
        ),
        ("timeout 285 tail --pid=4242 -f /tmp/open-swe-background-tasks/cmd-1/output.log", 300),
        ("tail -n 50 --follow=name /tmp/open-swe-background-tasks/cmd-1/output.log", 300),
        ("tail -n 50 -f /tmp/open-swe-background-tasks/cmd-1/output.log", 300),
        ("watch cat /tmp/open-swe-background-tasks/cmd-1/output.log", 300),
        ("until grep -q done /tmp/open-swe-background-tasks/cmd-1/output.log; do :; done", 300),
        ("bash -lc 'tail -f /tmp/open-swe-background-tasks/cmd-1/output.log'", 300),
        ("timeout -k 5 285 tail -f /tmp/open-swe-background-tasks/cmd-1/output.log", 300),
        ("bash -lc -- 'tail -f /tmp/open-swe-background-tasks/cmd-1/output.log'", 300),
        ("bash 2>/dev/null -lc 'tail -f /tmp/open-swe-background-tasks/cmd-1/output.log'", 300),
        (
            "tail -n 10 /tmp/open-swe-background-tasks/cmd-1/output.log; "
            "sleep 120 &&>/dev/null true",
            300,
        ),
        ("bash -l -o pipefail -c 'tail -f /tmp/open-swe-background-tasks/cmd-1/output.log'", 300),
        (
            "sleep 120 2>&1; tail -n 10 /tmp/open-swe-background-tasks/cmd-1/output.log",
            300,
        ),
        (
            "tail -n 10 /tmp/open-swe-background-tasks/cmd-1/output.log; sleep 120; "
            "tail -n 10 /tmp/open-swe-background-tasks/cmd-1/output.log",
            300,
        ),
    ],
)
async def test_a_foreground_command_past_the_limit_is_sent_to_the_background(
    monkeypatch: pytest.MonkeyPatch, command: str, timeout: int | float
) -> None:
    monkeypatch.setenv("OPEN_SWE_BACKEND_URL", "https://backend.invalid")
    for module in ("rt.middleware", "rt.tools", "rt.file_tools"):
        monkeypatch.setattr(f"{module}.run_id", lambda: "run-1")
    backend = _run_backend(catalog=Catalog(sandbox=_IN_SANDBOX))
    monkeypatch.setattr(backend.outbox, "started", MagicMock())
    monkeypatch.setattr(backend.outbox, "finished", MagicMock())
    request = ToolCallRequest(
        tool_call={
            "name": "execute",
            "args": {"command": command, "timeout": timeout},
            "id": "call-1",
            "type": "tool_call",
        },
        tool=None,
        state={"messages": []},
        runtime=ToolRuntime(
            state={"messages": []},
            context=None,
            config={},
            stream_writer=lambda event: None,
            tool_call_id="call-1",
            store=None,
        ),
    )
    in_sandbox = AsyncMock()
    files = FileToolsMiddleware(backend, 300)

    refused = await files.awrap_tool_call(request, in_sandbox)
    await backend.outbox.drain()

    in_sandbox.assert_not_awaited()
    assert isinstance(refused, ToolMessage) and refused.status == "error"
    assert "background_execute" in str(refused.content)


@pytest.mark.parametrize(
    "command",
    [
        "sleep 59; echo ready",
        "sleep 59 2>/dev/null; echo ready",
        "sleep 120 & echo ready",
        "sleep 120 &\necho ready",
        "sleep 120 >/dev/null 2>&1 &\ncat x",
        "sleep 120 >/tmp/run#42.log &",
        "sleep 120 >/dev/null 2>&1 & echo $!",
        "sleep 120 | cat & echo ready",
        "sleep 1; echo ready &",
        "tail -n 50 /tmp/open-swe-background-tasks/cmd-1/output.log",
        "grep -n 'sleep' /tmp/open-swe-background-tasks/cmd-1/output.log",
        "rg -n watch /tmp/open-swe-background-tasks/cmd-1/output.log",
        "bash -c 'grep -n sleep /tmp/open-swe-background-tasks/cmd-1/output.log'",
        "while IFS= read -r l; do printf '%s\\n' \"$l\"; done "
        "< /tmp/open-swe-background-tasks/cmd-1/output.log",
        "sleep 2; tail -n 50 /tmp/open-swe-background-tasks/cmd-1/output.log",
        "tail -- /tmp/open-swe-background-tasks/cmd-1/output.log -f",
        "cat /tmp/open-swe-background-tasks/cmd-1/output.log # while sleep watch",
        "cd /tmp/tri && sleep 2; cat output.log",
        "sleep 120 & tail -n 10 /tmp/open-swe-background-tasks/cmd-1/output.log",
        "sleep 120 >/dev/null &\ntail -n 10 /tmp/open-swe-background-tasks/cmd-1/output.log",
        "sleep 120 | cat & tail -n 10 /tmp/open-swe-background-tasks/cmd-1/output.log",
        "sleep 58 2>&1; tail -n 10 /tmp/open-swe-background-tasks/cmd-1/output.log",
        "tail -n 10 /tmp/open-swe-background-tasks/cmd-1/output.log > '/tmp/a b' 2>&1",
        "timeout -k 5 285 tail -n 10 /tmp/open-swe-background-tasks/cmd-1/output.log",
    ],
)
async def test_nonblocking_or_short_sleeps_reach_the_sandbox(
    monkeypatch: pytest.MonkeyPatch, command: str
) -> None:
    monkeypatch.setenv("OPEN_SWE_BACKEND_URL", "https://backend.invalid")
    backend = _run_backend(catalog=Catalog(sandbox=_IN_SANDBOX))
    request = ToolCallRequest(
        tool_call={
            "name": "execute",
            "args": {"command": command, "timeout": 300},
            "id": "call-1",
            "type": "tool_call",
        },
        tool=None,
        state={"messages": []},
        runtime=MagicMock(),
    )
    in_sandbox = AsyncMock(return_value=ToolMessage(content="ready", tool_call_id="call-1"))
    files = FileToolsMiddleware(backend, 300)

    result = await files._screened_run(request, in_sandbox)

    assert isinstance(result, ToolMessage) and result.content == "ready"


@pytest.mark.parametrize("excluded", [[], ["grep", "glob"]])
async def test_execute_searches_through_the_shell_where_grep_and_glob_are_excluded(
    monkeypatch: pytest.MonkeyPatch, excluded: list[str]
) -> None:
    """The model and code mode see an ``execute`` that sends searches to the shell, not
    to the ``grep`` and ``glob`` tools the run's catalog keeps from them."""
    from deepagents.middleware.filesystem import FilesystemMiddleware
    from langchain.agents.middleware.types import ModelRequest

    monkeypatch.setenv("OPEN_SWE_BACKEND_URL", "https://backend.invalid")
    backend = _run_backend(catalog=Catalog(excluded=excluded))
    tools = {tool.name: tool for tool in FilesystemMiddleware().tools}
    original = tools["execute"].description
    assert "Instead use the grep, glob tools" in original
    files = FileToolsMiddleware(backend, 300)
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
            assert "`git grep -n`" in description
            assert "never on the repository root" in description
            assert "no separate grep/glob tools" in description
            assert "Instead use the grep, glob tools" not in description
            assert "Use glob tool instead" not in description
            assert "Use grep tool instead" not in description
        else:
            assert description == original


@pytest.mark.parametrize(
    ("operations", "excluded", "disabled", "subagent", "expected"),
    [
        (
            [ToolSpec(name="background_execute", description="Run", parameters={"type": "object"})],
            [],
            [],
            False,
            True,
        ),
        (
            [ToolSpec(name="background_execute", description="Run", parameters={"type": "object"})],
            ["background_execute"],
            [],
            False,
            False,
        ),
        (
            [ToolSpec(name="background_execute", description="Run", parameters={"type": "object"})],
            [],
            ["background_execute"],
            False,
            False,
        ),
        (
            [ToolSpec(name="background_execute", description="Run", parameters={"type": "object"})],
            [],
            [],
            True,
            False,
        ),
        ([], [], [], False, False),
    ],
)
async def test_execute_recommends_background_execute_only_when_offered(
    monkeypatch: pytest.MonkeyPatch,
    operations: list[ToolSpec],
    excluded: list[str],
    disabled: list[str],
    subagent: bool,
    expected: bool,
) -> None:
    """``execute`` points long commands to ``background_execute`` only when this call offers it."""
    from deepagents.middleware.filesystem import FilesystemMiddleware
    from langchain.agents.middleware.types import ModelRequest

    monkeypatch.setenv("OPEN_SWE_BACKEND_URL", "https://backend.invalid")
    monkeypatch.setattr("rt.file_tools.namespace", lambda: ["subagent"] if subagent else [])
    backend = _run_backend(
        catalog=Catalog(operations=operations, excluded=excluded, disabled=disabled)
    )
    tools = {tool.name: tool for tool in FilesystemMiddleware().tools}
    files = FileToolsMiddleware(backend, 300)
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
        if expected:
            assert "background_execute" in description
            assert "300s (max 600s)" in description
        else:
            assert "background_execute" not in description


@pytest.mark.parametrize(
    ("timeout_arg", "elapsed", "exit_code", "background_execute", "subagent", "expected_timeout"),
    [
        (None, 300.1, -1, True, False, 300),
        (60.0, 60.1, -1, True, False, 60),
        (60, 60.1, -1, True, True, 60),
        (60, 60.1, -1, False, False, 60),
        (600, 600.1, -1, False, False, 600),
        (300, 2.0, -1, True, False, None),
        (300, 300.1, 1, True, False, None),
    ],
)
async def test_execute_explains_timeout_when_killed_at_deadline(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    timeout_arg: int | float | None,
    elapsed: float,
    exit_code: int,
    background_execute: bool,
    subagent: bool,
    expected_timeout: int | None,
) -> None:
    """An ``execute`` killed with ``exit_code == -1`` at its deadline gets a timeout explanation."""
    from deepagents.middleware.filesystem import FilesystemMiddleware
    from langchain.agents.middleware.types import ModelRequest
    from rt.backend import subagent_of

    monkeypatch.setenv("OPEN_SWE_BACKEND_URL", "https://backend.invalid")
    for module in ("rt.middleware", "rt.tools", "rt.file_tools"):
        monkeypatch.setattr(f"{module}.run_id", lambda: "run-1")
    now = 1000.0
    monkeypatch.setattr("rt.file_tools.monotonic", lambda: now)
    operations = (
        [ToolSpec(name="background_execute", description="Run", parameters={"type": "object"})]
        if background_execute
        else []
    )
    backend = _run_backend(catalog=Catalog(operations=operations, sandbox=_IN_SANDBOX))
    monkeypatch.setattr(backend.outbox, "started", MagicMock())
    monkeypatch.setattr(backend.outbox, "finished", MagicMock())
    seen_timeouts: list[int | None] = []

    class _TimedShell(LocalShellBackend):
        def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
            nonlocal now
            seen_timeouts.append(timeout)
            now += elapsed
            return ExecuteResponse(output="partial output", exit_code=exit_code, truncated=False)

    tools = {
        tool.name: tool
        for tool in FilesystemMiddleware(backend=_TimedShell(root_dir=tmp_path)).tools
    }
    files = FileToolsMiddleware(backend, 300)
    model_request = ModelRequest(
        model=FakeListChatModel(responses=["done"]),
        messages=[],
        tools=list(tools.values()),
        state={"messages": []},
    )
    await files.awrap_model_call(model_request, AsyncMock())
    args: dict[str, object] = {"command": "python3 train.py"}
    if timeout_arg is not None:
        args["timeout"] = timeout_arg
    runtime = ToolRuntime(
        state={"messages": []},
        context=None,
        config={},
        stream_writer=lambda event: None,
        tool_call_id="call-1",
        store=None,
    )
    request = ToolCallRequest(
        tool_call={"name": "execute", "args": args, "id": "call-1", "type": "tool_call"},
        tool=tools["execute"],
        state={"messages": []},
        runtime=runtime,
    )

    if subagent:
        with subagent_of("task-1"):
            result = await files.awrap_tool_call(request, files._run_own)
    else:
        result = await files.awrap_tool_call(request, files._run_own)

    assert seen_timeouts == [None if timeout_arg is None else int(timeout_arg)]
    expected_prefix = f"partial output\n[Command failed with exit code {exit_code}]"
    assert isinstance(result, ToolMessage)
    assert result.status == "success"
    assert result.artifact == {"exit_code": exit_code, "truncated": False}
    if expected_timeout is None:
        assert result.content == expected_prefix
    else:
        assert isinstance(result.content, str)
        assert result.content.startswith(expected_prefix)
        assert f"timed out after {expected_timeout}s" in result.content
        if background_execute and not subagent:
            assert "background_execute" in result.content
        else:
            assert "background_execute" not in result.content
            assert ("up to 600s" in result.content) is (expected_timeout < 600)


async def test_a_command_code_mode_runs_is_put_to_the_guards_too(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Code mode calls tools past the tool-call chain, so its shell tool checks each call itself."""
    from langchain.agents.middleware.types import ModelRequest

    monkeypatch.setenv("OPEN_SWE_BACKEND_URL", "https://backend.invalid")
    for module in ("rt.middleware", "rt.tools", "rt.file_tools"):
        monkeypatch.setattr(f"{module}.run_id", lambda: "run-1")
    backend = _run_backend(
        catalog=Catalog(files=FilesSpec(guards=["shell_commands"]), sandbox=_IN_SANDBOX)
    )
    refusal = ShellVerdict(allowed=False, refusal="pr_creation_fallback_blocked")
    monkeypatch.setattr(backend, "post", AsyncMock(return_value=refusal.model_dump(mode="json")))
    monkeypatch.setattr(backend.outbox, "started", MagicMock())
    monkeypatch.setattr(backend.outbox, "finished", MagicMock())
    ran = AsyncMock(return_value="opened")

    @tool("execute")
    async def sandbox_execute(command: str) -> str:
        """Run a command in the sandbox."""
        return await ran(command)

    files = FileToolsMiddleware(backend, 300)
    model_request = MagicMock(spec=ModelRequest)
    model_request.tools = [sandbox_execute]
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


async def test_hub_skill_routes_recheck_scope_and_refresh_between_runs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from deepagents.backends import CompositeBackend, FilesystemBackend
    from deepagents.middleware.skills import SkillsState
    from langgraph.runtime import Runtime
    from langsmith.schemas import AgentContext, FileEntry
    from rt.skills import HubSkillRoute, RunSkillRepos, RunSkillsMiddleware
    from runtime_api.skills import RunSkills

    monkeypatch.setenv("OPEN_SWE_BACKEND_URL", "https://backend.invalid")
    monkeypatch.setenv("LANGSMITH_API_KEY", "test-service-key")
    monkeypatch.setenv("LANGSMITH_ENDPOINT", "https://smith.invalid")
    monkeypatch.setenv("LANGSMITH_WORKSPACE_ID", "00000000-0000-0000-0000-000000000001")
    current = _run_backend().run
    monkeypatch.setattr("rt.backend.current_run", lambda: current)
    backend = OpenSweBackend()
    owner: UUID | None = UUID(int=1)

    async def answer(name: str, body: dict[str, JsonValue]) -> BaseModel:
        assert name == "skills"
        # Mounted exactly as the backend names it, with no runtime configuration.
        return RunSkills(
            organization_repo="-/robin-test",
            personal_repo=None if owner is None else f"-/robin-test-user-{owner}",
        )

    _answering(monkeypatch, backend, answer)
    client = MagicMock(spec=Client)
    snapshot = AgentContext(
        commit_id=UUID(int=1),
        commit_hash="1" * 64,
        files={
            "deploy/SKILL.md": FileEntry(
                content="---\nname: deploy\ndescription: First version\n---\nPrivate instructions"
            ),
            "deploy/reference.md": FileEntry(content="Private reference"),
        },
    )
    client.pull_agent.return_value = snapshot
    monkeypatch.setattr("rt.skills.Client", MagicMock(return_value=client))
    monkeypatch.setattr("rt.skills.run_id", lambda: "run-1")
    route = HubSkillRoute(RunSkillRepos(backend), personal=True)
    native = CompositeBackend(
        default=FilesystemBackend(root_dir=str(tmp_path), virtual_mode=True),
        routes={"/my-skills/": route},
    )
    middleware = RunSkillsMiddleware(native)
    previous: SkillsState = {"messages": [], "skills_metadata": []}
    for index, next_owner in enumerate((UUID(int=1), None, UUID(int=2), UUID(int=1))):
        owner = next_owner
        current = _run_backend().run
        snapshot.files["deploy/SKILL.md"] = FileEntry(
            content=f"---\nname: deploy\ndescription: Version {index}\n---\nPrivate instructions"
        )
        discovered = await middleware.abefore_agent(previous, Runtime(), {})
        assert discovered is not None
        metadata = discovered["skills_metadata"]
        assert len(metadata) == (1 if owner is not None else 0)
        if owner is not None:
            assert metadata[0]["description"] == f"Version {index}"
        previous["skills_metadata"] = metadata
        listed = await native.als("/my-skills/")
        read = await native.aread("/my-skills/deploy/SKILL.md")
        reference = await native.aread("/my-skills/deploy/reference.md")
        [downloaded] = await native.adownload_files(["/my-skills/deploy/reference.md"])
        if owner is None:
            assert listed.entries == []
            assert read.error and reference.error and downloaded.error == "permission_denied"
            assert (await native.agrep("Private", "/my-skills/")).matches == []
            assert (await native.aglob("**", "/my-skills/")).matches == []
        else:
            assert listed.entries
            assert read.file_data is not None
            assert f"Version {index}" in read.file_data["content"]
            assert read.file_data is not None and reference.file_data is not None
            assert downloaded.content == b"Private reference"
            assert client.pull_agent.call_args.args[0] == f"-/robin-test-user-{owner}"
        assert (await route.awrite("/deploy/SKILL.md", "overwrite")).error
        assert (await route.aedit("/deploy/SKILL.md", "Private", "Public")).error
        assert (await route.adelete("/deploy/SKILL.md")).error
        assert (await route.aupload_files([("/deploy/SKILL.md", b"overwrite")]))[
            0
        ].error == "permission_denied"
    assert client.pull_agent.call_count == 3
    client.push_agent.assert_not_called()


@pytest.mark.parametrize("code_mode", [False, True])
@pytest.mark.parametrize(
    "source", ["/memories/user/projects", "/skills/deploy", "/organization-skills/review"]
)
async def test_native_files_stay_in_mda_without_a_sandbox(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, code_mode: bool, source: str
) -> None:
    from deepagents.backends import FilesystemBackend
    from deepagents.middleware.filesystem import FilesystemMiddleware
    from langchain.agents.middleware.types import ModelRequest
    from managed_deepagents import ManagedToolRuntime

    monkeypatch.setenv("OPEN_SWE_BACKEND_URL", "https://backend.invalid")
    for module in ("rt.middleware", "rt.tools", "rt.file_tools"):
        monkeypatch.setattr(f"{module}.run_id", lambda: "run-1")
    backend = _run_backend(
        catalog=Catalog(
            files=FilesSpec(checks=["agents_md"]),
            memory=MemoryScope(),
        )
    )
    monkeypatch.setattr(backend.outbox, "started", MagicMock())
    monkeypatch.setattr(backend.outbox, "finished", MagicMock())
    filesystem = FilesystemBackend(root_dir=str(tmp_path), virtual_mode=True)
    tools = {tool.name: tool for tool in FilesystemMiddleware(backend=filesystem).tools}
    files = FileToolsMiddleware(backend, 300)
    model_request = MagicMock(spec=ModelRequest)
    model_request.tools = list(tools.values())
    await files.awrap_model_call(model_request, AsyncMock())
    runtime = ManagedToolRuntime(
        state={"messages": []},
        context=None,
        config={},
        stream_writer=lambda event: None,
        tool_call_id="call-1",
        store=None,
        backend=filesystem,  # ty: ignore[invalid-argument-type] - a stand-in for the sandbox
    )
    for name, args in (
        (
            "write_file",
            {"file_path": f"{source}/AGENTS.md", "content": "Remember 731"},
        ),
        (
            "write_file",
            {"file_path": f"{source}/notes.md", "content": "Project notes"},
        ),
        ("read_file", {"file_path": f"{source}/notes.md"}),
    ):
        if code_mode:
            [code_tool] = files.code_tools([name])
            result = await code_tool.ainvoke({**args, "runtime": runtime})
        else:
            request = ToolCallRequest(
                tool_call={"name": name, "args": args, "id": "call-1", "type": "tool_call"},
                tool=tools[name],
                state={"messages": []},
                runtime=runtime,
            )
            result = await files.awrap_tool_call(request, files._run_own)
        assert isinstance(result, ToolMessage) and result.status != "error"
    assert "Project notes" in result.text
    assert f"Instructions from: {source}/AGENTS.md\nRemember 731" in result.text
    assert (tmp_path / source.lstrip("/") / "AGENTS.md").read_text() == "Remember 731"


@pytest.mark.parametrize("code_mode", [False, True])
async def test_each_run_lists_only_the_skills_it_may_see_and_reads_them_here(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, code_mode: bool
) -> None:
    from deepagents.backends import CompositeBackend, FilesystemBackend
    from deepagents.middleware.filesystem import FilesystemMiddleware
    from runtime_api.loop import Done
    from runtime_api.skills import RunSkills

    monkeypatch.setenv("OPEN_SWE_BACKEND_URL", "https://backend.invalid")
    for module in ("rt.middleware", "rt.tools", "rt.file_tools"):
        monkeypatch.setattr(f"{module}.run_id", lambda: "run-1")
    backend = _run_backend(catalog=Catalog(skill_sources=["/my-skills/"], sandbox=_IN_SANDBOX))
    asked: list[str] = []

    async def answer(name: str, body: dict[str, JsonValue]) -> BaseModel:
        asked.append(name)
        if name != "skills":
            return Done()
        return RunSkills()

    _answering(monkeypatch, backend, answer)
    folder = tmp_path / "deploy"
    folder.mkdir()
    (folder / "SKILL.md").write_text(
        "---\nname: deploy\ndescription: Ship it\n---\n\nRun make deploy.\n"
    )
    (folder / "reference.md").write_text("Reference")
    native = CompositeBackend(
        default=FilesystemBackend(root_dir=str(tmp_path / "absent"), virtual_mode=True),
        routes={"/my-skills/": FilesystemBackend(root_dir=str(tmp_path), virtual_mode=True)},
    )
    files = FileToolsMiddleware(
        backend,
        300,
    )
    runtime = ToolRuntime(
        state={"messages": []},
        context=None,
        config={},
        stream_writer=lambda event: None,
        tool_call_id="call-1",
        store=None,
    )
    tools = {tool.name: tool for tool in FilesystemMiddleware(backend=native).tools}
    request = MagicMock()
    request.tools = list(tools.values())
    await files.awrap_model_call(request, AsyncMock())
    collector = RunCollectorCallbackHandler()

    async def read_skill(path: str) -> ToolMessage | Command:
        if code_mode:
            [read_tool] = files.code_tools(["read_file"])
            return await read_tool.ainvoke({"file_path": path, "runtime": runtime})
        return await files.awrap_tool_call(
            ToolCallRequest(
                tool_call={
                    "name": "read_file",
                    "args": {"file_path": path},
                    "id": "call-1",
                    "type": "tool_call",
                },
                tool=None,
                state={"messages": []},
                runtime=runtime,
            ),
            files._run_own,
        )

    reader = RunnableLambda(read_skill)
    client = MagicMock(spec=Client)
    tracer = LangChainTracer(client=client, project_name="test")
    config: RunnableConfig = {"callbacks": [tracer, collector]}
    with tracing_context(enabled=True):
        read = await reader.ainvoke("/my-skills/deploy/SKILL.md", config=config)
        await reader.ainvoke("/my-skills/deploy/reference.md", config=config)
        missing = await reader.ainvoke("/my-skills/deleted/SKILL.md", config=config)
    assert isinstance(missing, ToolMessage) and missing.status == "error"
    await backend.outbox.drain()
    assert len(collector.traced_runs) == 3
    patches = {call.kwargs["run_id"]: call.kwargs for call in client.update_run.call_args_list}
    for index, root in enumerate(collector.traced_runs):
        pending = list(root.child_runs)
        reads = []
        while pending:
            span = pending.pop()
            pending.extend(span.child_runs)
            if span.name == "read_file" and not span.child_runs:
                reads.append(span)
        assert len(reads) == 1
        assert _run_metadata(patches[reads[0].id]).get("ls_skill_name") == (
            "deploy" if index == 0 else None
        )
        assert "ls_skill_name" not in _run_metadata(patches[root.id])

    assert isinstance(read, ToolMessage) and "Run make deploy." in read.text
    assert "skills" not in asked
    assert not [name for name in asked if name.startswith("files.")]


@pytest.mark.parametrize(
    ("path", "skill"),
    [
        ("/workspace/repo/.agents/skills/review/SKILL.md", "review"),
        ("/workspace/repo/.claude/skills/deploy/SKILL.md", "deploy"),
        ("/workspace/repo/docs/SKILL.md", None),
        ("/workspace/repo/.agents/skills/review/reference.md", None),
    ],
)
@pytest.mark.parametrize("failure", [None, "message", "text", "exception"])
async def test_repository_skill_reads_are_tagged_without_changing_the_shared_tool(
    monkeypatch: pytest.MonkeyPatch, path: str, skill: str | None, failure: str | None
) -> None:
    monkeypatch.setenv("OPEN_SWE_BACKEND_URL", "https://backend.invalid")
    backend = _run_backend(catalog=Catalog(sandbox=_IN_SANDBOX))
    monkeypatch.setattr(backend.outbox, "started", MagicMock())
    monkeypatch.setattr(backend.outbox, "finished", MagicMock())

    @tool
    async def read_file(file_path: str) -> str | ToolMessage:
        """Read a file."""
        if failure == "exception":
            raise FileNotFoundError(file_path)
        if failure == "message":
            return ToolMessage(content="Error: not found", tool_call_id="read", status="error")
        if failure == "text":
            return "Error: not found"
        return file_path

    files = FileToolsMiddleware(backend, 300)
    model = _ToolCallingModel(
        messages=iter(
            [
                AIMessage(
                    "",
                    tool_calls=[{"name": "read_file", "args": {"file_path": path}, "id": "read"}],
                ),
                AIMessage("done"),
            ]
        )
    )
    collector = RunCollectorCallbackHandler()
    graph = create_agent(model, tools=[read_file], middleware=[files])
    client = MagicMock(spec=Client)
    tracer = LangChainTracer(client=client, project_name="test")
    with tracing_context(enabled=True):
        await graph.ainvoke({"messages": [("user", "read")]}, {"callbacks": [tracer, collector]})
    patches = {call.kwargs["run_id"]: call.kwargs for call in client.update_run.call_args_list}
    pending = list(collector.traced_runs)
    reads = []
    while pending:
        span = pending.pop()
        pending.extend(span.child_runs)
        if span.name == "read_file":
            reads.append(span)
    assert len(reads) == 1
    assert _run_metadata(patches[reads[0].id]).get("ls_skill_name") == (
        skill if failure is None else None
    )
    assert read_file.metadata is None


class _ToolCallingModel(GenericFakeChatModel):
    def bind_tools(self, tools: object, **kwargs: object) -> Self:
        return self


_ROLE = '<dynamic-context kind="run" id="task-role">You coordinate.</dynamic-context>'
COORDINATOR_CATALOG = Catalog(
    skill_sources=["/skills/coordinator/"],
    loop=LoopRules(task_context=TaskContext(instructions=_ROLE)),
    sandbox=_IN_SANDBOX,
)


async def _launch_worker(
    monkeypatch: pytest.MonkeyPatch,
    client: MagicMock,
    *,
    also: Sequence[ToolCall] = (),
    task_context: str | None = None,
    backend: OpenSweBackend | None = None,
) -> tuple[list[ToolMessage], list[tuple[str, dict[str, object]]], Mapping[str, object]]:
    """Run a coordinator that launches a worker through deepagents' ``start_async_task``;
    registering it answers with ``task_context``."""
    from deepagents.middleware import async_subagents
    from rt.middleware import AsyncWorkerLaunchMiddleware

    monkeypatch.setenv("OPEN_SWE_BACKEND_URL", "https://backend.invalid")
    backend = backend or _run_backend("coordinator-token", catalog=COORDINATOR_CATALOG)
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
        return {
            "worker": {"worker_thread_id": body["worker_thread_id"], "task_id": "task-1"},
            "task_context": task_context,
        }

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

    held: dict[str, object] = {}

    class HeldState(AgentMiddleware[RunState]):
        """Keeps the run's held state as the model call after the launch sees it."""

        state_schema = RunState

        async def abefore_model(self, state: RunState, runtime: object) -> None:
            held.update(state.get(STATE_KEY, {}))

    # Middleware types are invariant in the state they extend; a graph merges them all.
    middleware = cast(
        list[AgentMiddleware], [HeldState(), AsyncWorkerLaunchMiddleware(backend), launches]
    )
    graph = create_agent(model, middleware=middleware)
    values = await graph.ainvoke(
        {"messages": [("user", "fix login in the background")]},
        {
            "configurable": {"thread_id": "coordinator-thread", "assistant_id": "open-swe"},
            "metadata": {"assistant_id": "open-swe"},
        },
        # The launch middleware reads this context, though it declares no context type.
        context={  # ty: ignore[invalid-argument-type]
            "graph": "agent",
            "__run_token": "coordinator-token",
            "origin": "github",
            "owner_ls_user_id": "original-owner",
            "visibility": "private",
        },
    )
    # Open SWE's task messages report the worker; deepagents does not poll it too.
    assert not values.get("async_tasks")
    messages = [m for m in values["messages"] if isinstance(m, ToolMessage)]
    return messages, calls, held


async def test_a_launched_worker_carries_a_grant_never_its_coordinators_run_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = MagicMock()
    client.threads.create = AsyncMock(return_value={"thread_id": "worker-thread"})
    client.runs.create = AsyncMock(return_value={"run_id": "worker-run"})

    results, calls, _ = await _launch_worker(
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
        "__catalog": KINDS["agent"].applied(COORDINATOR_CATALOG).model_dump(mode="json"),
        "origin": "github",
        "owner_ls_user_id": "original-owner",
        "visibility": "private",
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


async def test_a_coordinators_first_worker_tells_its_run_the_task_it_joined(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = MagicMock()
    client.threads.create = AsyncMock(return_value={"thread_id": "worker-thread"})
    client.runs.create = AsyncMock(return_value={"run_id": "worker-run"})
    monkeypatch.setenv("OPEN_SWE_BACKEND_URL", "https://backend.invalid")
    backend = _run_backend("coordinator-token", catalog=COORDINATOR_CATALOG)
    joined = '<dynamic-context kind="task" id="task-1">Fix login</dynamic-context>'

    _, _, held = await _launch_worker(monkeypatch, client, task_context=joined, backend=backend)

    # The run's later model calls tell the model the task, from its checkpoint: a run
    # resumed elsewhere still knows it.
    assert held == {JOINED_TASK_CONTEXT: joined}


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

    [result], calls, _ = await _launch_worker(monkeypatch, client)

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
                "invocation_id": None,
            }
            adopted = {
                "run_token": "worker-token",
                "catalog": {"excluded": ["slack_reply"]},
                "invocation_id": "worker-invocation",
            }
            return httpx2.Response(200, request=request, json=adopted)
        if hook == REPORTS.name:
            taken = ReportsTaken(replies=[{} for _ in Reports.model_validate(json).items])
            return httpx2.Response(200, request=request, json=taken.model_dump(mode="json"))
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


async def test_a_resumed_worker_readopts_without_before_agent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from langchain_core.runnables.config import var_child_runnable_config
    from langgraph.prebuilt.tool_node import ToolCallRequest
    from rt.file_tools import FileToolsMiddleware
    from rt.run_tools import RunToolsets
    from runtime_api.catalog import ToolSpec

    monkeypatch.setenv("OPEN_SWE_BACKEND_URL", "https://backend.invalid")
    monkeypatch.setattr("rt.middleware.run_id", lambda: "worker-run")
    request = httpx2.Request("POST", "https://backend.invalid")
    seen: list[tuple[str, str, dict[str, object]]] = []
    worker_tool = ToolSpec(name="ping", description="Ping", parameters={"type": "object"})

    async def post(
        url: str, *, headers: dict[str, str], json: dict[str, object], **kwargs: object
    ) -> httpx2.Response:
        hook = url.rsplit("/", 1)[-1]
        seen.append((hook, headers["Authorization"], json))
        if hook == "adopt":
            adopted = {
                "run_token": "worker-token",
                "catalog": Catalog(operations=[worker_tool], inbox=_INBOX).model_dump(mode="json"),
                "invocation_id": json["invocation_id"],
            }
            return httpx2.Response(200, request=request, json=adopted)
        if hook == "inbox":
            return httpx2.Response(200, request=request, json={})
        return httpx2.Response(403, request=request, json={"detail": "unexpected"})

    client = MagicMock(is_closed=False)
    client.post = AsyncMock(side_effect=post)
    monkeypatch.setattr("rt.backend.httpx2.AsyncClient", lambda **kwargs: client)

    coordinator_catalog = Catalog(operations=[worker_tool], inbox=_INBOX).names_only()
    backend = _run_backend(None, worker_grant="worker-grant", catalog=coordinator_catalog)
    middleware = BackendRunMiddleware(backend)
    toolsets = RunToolsets(backend, FileToolsMiddleware(backend, 300))
    state: RunState = {
        "messages": [HumanMessage(content="Fix login")],
        "open_swe_prepared_for": "worker-invocation",
    }
    tool_request = ToolCallRequest(
        tool_call={"name": "ping", "args": {}, "id": "call-1", "type": "tool_call"},
        tool=None,
        state=state,
        runtime=MagicMock(),
    )

    async def run_tool(_req: ToolCallRequest) -> ToolMessage:
        tools = await toolsets.ready()
        assert "ping" in tools.by_name
        return ToolMessage(content="ok", tool_call_id="call-1", name="ping")

    token = var_child_runnable_config.set(
        {"configurable": {"thread_id": "worker-thread", "run_id": "worker-run"}}
    )
    try:
        # Parallel tool calls on a resumed worker adopt once, then inbox uses the adopted token.
        await asyncio.gather(
            middleware.awrap_tool_call(tool_request, run_tool),
            middleware.awrap_tool_call(tool_request, run_tool),
        )
        await middleware.abefore_model(state, MagicMock())
    finally:
        var_child_runnable_config.reset(token)

    assert [(hook, bearer) for hook, bearer, _ in seen] == [
        ("adopt", "Bearer worker-grant"),
        ("inbox", "Bearer worker-token"),
    ]
    assert seen[0][2]["invocation_id"] == "worker-invocation"
    assert backend.invocation_id == "worker-invocation"


async def test_a_resumed_worker_preserves_invocation_when_inner_middleware_runs_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from typing import cast

    from langchain_core.runnables.config import var_child_runnable_config
    from langchain_quickjs.middleware import REPLState
    from rt.file_tools import FileToolsMiddleware
    from rt.run_tools import RunCodeInterpreter, RunToolsets, RunToolsMiddleware
    from runtime_api.catalog import ToolSpec

    monkeypatch.setenv("OPEN_SWE_BACKEND_URL", "https://backend.invalid")
    request = httpx2.Request("POST", "https://backend.invalid")
    worker_tool = ToolSpec(name="ping", description="Ping", parameters={"type": "object"})
    coordinator_catalog = Catalog(operations=[worker_tool]).names_only()
    seen: list[dict[str, object]] = []

    async def post(
        url: str, *, headers: dict[str, str], json: dict[str, object], **kwargs: object
    ) -> httpx2.Response:
        assert url.rsplit("/", 1)[-1] == "adopt"
        assert headers["Authorization"] == "Bearer worker-grant"
        seen.append(json)
        adopted = {
            "run_token": "worker-token",
            "catalog": Catalog(operations=[worker_tool]).model_dump(mode="json"),
            "invocation_id": json["invocation_id"],
        }
        return httpx2.Response(200, request=request, json=adopted)

    client = MagicMock(is_closed=False)
    client.post = AsyncMock(side_effect=post)
    monkeypatch.setattr("rt.backend.httpx2.AsyncClient", lambda **kwargs: client)

    state: RunState = {
        "messages": [HumanMessage(content="Fix login")],
        "open_swe_prepared_for": "worker-invocation",
    }
    token = var_child_runnable_config.set(
        {"configurable": {"thread_id": "worker-thread", "run_id": "worker-run"}}
    )
    try:
        tools_backend = _run_backend(None, worker_grant="worker-grant", catalog=coordinator_catalog)
        tools_sets = RunToolsets(tools_backend, FileToolsMiddleware(tools_backend, 300))
        await RunToolsMiddleware(tools_sets).abefore_model(state, MagicMock())

        code_backend = _run_backend(None, worker_grant="worker-grant", catalog=coordinator_catalog)
        code_sets = RunToolsets(code_backend, FileToolsMiddleware(code_backend, 300))
        await RunCodeInterpreter(code_sets, active=lambda _tools: False).aafter_agent(
            cast(REPLState, state), MagicMock()
        )
    finally:
        var_child_runnable_config.reset(token)

    assert [body["invocation_id"] for body in seen] == ["worker-invocation", "worker-invocation"]
    assert tools_backend.invocation_id == "worker-invocation"
    assert code_backend.invocation_id == "worker-invocation"


@pytest.mark.parametrize(
    ("status", "message", "retries"),
    [
        (400, "No tool output found for function call call_1", False),
        (400, "No tool call found for function call output call_1", False),
        (400, "unexpected `tool_use_id` found in `tool_result` blocks", False),
        (400, "tool_use ids were found without `tool_result` blocks", False),
        (400, "context window exceeded", False),
        (500, "No tool output found for function call", True),
    ],
)
async def test_mda_retries_only_transient_errors_on_the_fallback(
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


async def test_a_model_call_paused_for_a_connection_is_not_retried_on_the_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from langchain.agents.middleware.types import ModelRequest, ModelResponse

    monkeypatch.setenv("OPEN_SWE_BACKEND_URL", "https://backend.invalid")
    model = FakeListChatModel(responses=["done"])
    monkeypatch.setattr("rt.middleware.build_model", lambda *args, **kwargs: model)
    paused = GraphInterrupt()
    handler = AsyncMock(side_effect=[paused, ModelResponse(result=[AIMessage(content="fallback")])])
    state: RunState = {
        "messages": [],
        "open_swe_call": ModelCall(
            system_prompt="test",
            model=None,
            fallback=ModelSpec(model_id="fallback", kwargs={}),
        ).model_dump(mode="json"),
    }
    middleware = BackendRunMiddleware(_run_backend("token", invocation_id="run"))
    with pytest.raises(GraphInterrupt) as caught:
        await middleware.awrap_model_call(
            ModelRequest(model=model, messages=[], state=state), handler
        )
    assert caught.value is paused


def _planned_with_fallback() -> RunState:
    return {
        "messages": [],
        "open_swe_call": ModelCall(
            system_prompt="test",
            model=ModelSpec(model_id="primary", kwargs={}),
            fallback=ModelSpec(model_id="fallback", kwargs={}),
        ).model_dump(mode="json"),
    }


def _reporting_backend(
    monkeypatch: pytest.MonkeyPatch, reported: list[tuple[str, dict[str, JsonValue]]]
) -> OpenSweBackend:
    backend = _run_backend("token", invocation_id="run")

    async def answer(name: str, body: dict[str, JsonValue]) -> BaseModel:
        reported.append((name, body))
        return Done()

    _answering(monkeypatch, backend, answer)
    monkeypatch.setattr("rt.middleware.run_id", lambda: "run-1")
    return backend


async def test_a_model_call_that_fails_the_run_reports_why_before_it_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from langchain.agents.middleware.types import ModelRequest

    monkeypatch.setenv("OPEN_SWE_BACKEND_URL", "https://backend.invalid")
    model = FakeListChatModel(responses=["done"])
    monkeypatch.setattr("rt.middleware.build_model", lambda *args, **kwargs: model)
    body: dict[str, object] = {"error": {"code": "context_length_exceeded"}}
    request = httpx2.Request("POST", "https://api.openai.com/v1/responses")
    error = openai.BadRequestError(
        "too long", response=httpx2.Response(400, request=request, json=body), body=body
    )
    reported: list[tuple[str, dict[str, JsonValue]]] = []
    middleware = BackendRunMiddleware(_reporting_backend(monkeypatch, reported))

    with pytest.raises(openai.BadRequestError) as caught:
        await middleware.awrap_model_call(
            ModelRequest(model=model, messages=[], state=_planned_with_fallback()),
            AsyncMock(side_effect=error),
        )

    assert caught.value is error
    # Taken by the backend before the run fails, so the completion webhook finds it.
    assert reported == [
        (
            "model_failed",
            ModelFailed(
                run_id="run-1", code="context_too_long", error_type="BadRequestError"
            ).model_dump(mode="json"),
        )
    ]


async def test_a_model_call_paused_for_a_connection_reports_no_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from langchain.agents.middleware.types import ModelRequest

    monkeypatch.setenv("OPEN_SWE_BACKEND_URL", "https://backend.invalid")
    model = FakeListChatModel(responses=["done"])
    monkeypatch.setattr("rt.middleware.build_model", lambda *args, **kwargs: model)
    reported: list[tuple[str, dict[str, JsonValue]]] = []
    middleware = BackendRunMiddleware(_reporting_backend(monkeypatch, reported))

    with pytest.raises(GraphInterrupt):
        await middleware.awrap_model_call(
            ModelRequest(model=model, messages=[], state=_planned_with_fallback()),
            AsyncMock(side_effect=GraphInterrupt()),
        )
    assert reported == []


async def test_a_lasting_provider_outage_ends_the_run_with_a_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from langchain.agents.middleware.types import ExtendedModelResponse, ModelRequest

    monkeypatch.setenv("OPEN_SWE_BACKEND_URL", "https://backend.invalid")
    monkeypatch.setattr("rt.model_calls.BACKOFF_SCHEDULE", (0.0, 0.0))
    model = FakeListChatModel(responses=["done"])
    monkeypatch.setattr("rt.middleware.build_model", lambda *args, **kwargs: model)
    reported: list[tuple[str, dict[str, JsonValue]]] = []
    middleware = BackendRunMiddleware(_reporting_backend(monkeypatch, reported))
    handler = AsyncMock(side_effect=TimeoutError("overloaded"))

    response = await middleware.awrap_model_call(
        ModelRequest(model=model, messages=[], state=_planned_with_fallback()), handler
    )

    # Its transcript report rides with it into the checkpoint, as a model's reply does.
    assert isinstance(response, ExtendedModelResponse)
    assert response.model_response.result[0].content == MODEL_OUTAGE_MESSAGE
    assert handler.await_count == 3
    assert reported == []


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
            RootAgentTraceMiddleware(),
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
    assert {"prepare", "tool_started", "tool_result", "run_ended"} <= sent.keys()
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
            (
                "BackendRunMiddleware.",
                "RuntimeToolsMiddleware.",
                "RunModelCallLimit.",
                "RootAgentTraceMiddleware.",
            )
        )
    ]
    assert any(run["name"] == "RootAgentTraceMiddleware.awrap_model_call" for run in hooks)
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


class _ScriptedAnthropic(ChatAnthropic):
    replies: list[AIMessage] = []

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: object,
    ) -> ChatResult:
        return ChatResult(generations=[ChatGeneration(message=self.replies.pop(0))])


async def test_an_agents_own_calls_are_its_root_agent_and_a_launched_runs_are_a_subagents() -> None:
    """Robin traces the calls an agent answers with as LangSmith's root agent, and not the calls
    a tool makes. A worker's run that MDA nests in its coordinator's trace is a subagent there,
    so its calls stay out of the coordinator's conversation."""
    from deepagents.middleware import async_subagents

    in_tool: list[object] = []

    @tool
    def look() -> str:
        """Look into it."""
        in_tool.append((get_tracing_context()["metadata"] or {}).get("ls_agent_type"))
        return "found it"

    client = MagicMock(spec=Client)
    launch = RunTree(name="start_async_task", run_type="tool", ls_client=client)
    # deepagents' trace headers for a launched run, as the Agent Server keeps them.
    nested = {
        "langsmith-trace": launch.dotted_order,
        "langsmith-metadata": {async_subagents._NEST_UNDER_PARENT: True},
    }
    # Whom the run bills to, once the worker adopted its run token.
    billing = {"billing_bucket": "user", "billed_user_email": "human@example.com"}
    traced: dict[str, list[tuple[bool, object, object]]] = {}
    for run, headers, metadata in [
        ("dashboard", nested, {}),
        ("launched", {**nested, "deepagents_parent": {"thread_id": "lead"}}, {}),
        ("forwarded context", nested, {"parent_thread_id": "lead"}),
        ("lead's own", nested, {"parent_thread_id": "worker"}),
        ("resumed worker", {}, {"parent_thread_id": "lead"}),
        ("own", {}, {}),
    ]:
        client.create_run.reset_mock()
        model = _ScriptedAnthropic(
            model_name="claude-test",
            api_key="test",
            replies=[
                AIMessage(content="", tool_calls=[ToolCall(name="look", args={}, id="call-1")]),
                AIMessage(content="done"),
            ],
        )
        graph = create_agent(
            model=model,
            tools=[look],
            middleware=[RootAgentTraceMiddleware(lambda: {**billing, "origin": "dashboard"})],
        )
        config: RunnableConfig = {
            "configurable": {"thread_id": "worker", **headers},
            "metadata": metadata,
            "callbacks": [LangChainTracer(client=client, project_name="test")],
        }
        with tracing_context(enabled=True), _parent_trace(config):
            await graph.ainvoke({"messages": [HumanMessage("Look into it.")]}, config)
        traced[run] = [
            (
                str(created.kwargs["trace_id"]) == str(launch.trace_id),
                _run_metadata(created.kwargs).get("ls_agent_type"),
                _run_metadata(created.kwargs).get("billed_user_email"),
            )
            for created in client.create_run.call_args_list
            if created.kwargs["run_type"] == "llm"
        ]

    # Only a nested worker's calls take the billing its run's own metadata lacks.
    worker = "human@example.com"
    assert traced == {
        "dashboard": [(True, "root", None)] * 2,
        "launched": [(True, "subagent", worker)] * 2,
        "forwarded context": [(True, "subagent", worker)] * 2,
        "lead's own": [(True, "root", None)] * 2,
        "resumed worker": [(False, "root", None)] * 2,
        "own": [(False, "root", None)] * 2,
    }
    # MDA tags a nested worker's whole run, so the calls its tools make are subagent's too.
    assert in_tool == [None, "subagent", "subagent", None, None, None]


async def test_a_subagent_keeps_its_own_tag_though_it_inherits_the_root_agent_middleware() -> None:
    """deepagents runs a forked subagent with a copy of its parent's middleware, inside a
    tracing context that tags it ``subagent``; its calls must not join the root conversation."""
    client = MagicMock(spec=Client)
    model = _ScriptedAnthropic(
        model_name="claude-test", api_key="test", replies=[AIMessage(content="done")]
    )
    graph = create_agent(model=model, middleware=[RootAgentTraceMiddleware()])
    config: RunnableConfig = {"callbacks": [LangChainTracer(client=client, project_name="test")]}
    with tracing_context(enabled=True, metadata={"ls_agent_type": "subagent"}):
        await graph.ainvoke({"messages": [HumanMessage("Look into it.")]}, config)

    assert [
        _run_metadata(created.kwargs).get("ls_agent_type")
        for created in client.create_run.call_args_list
        if created.kwargs["run_type"] == "llm"
    ] == ["subagent"]
