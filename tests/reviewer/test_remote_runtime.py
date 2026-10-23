import json
import time

import jwt
import pytest
from starlette.types import Message, Receive, Scope, Send

from openswe.remote_runtime.server import RunTokenMiddleware
from openswe.remote_runtime.tokens import (
    RemoteRun,
    RuntimeTokenError,
    WorkerGrant,
    sign_runtime_token,
    sign_worker_grant,
    verify_runtime_token,
    verify_worker_grant,
)

RUN = RemoteRun(
    thread_id="thread-1",
    assistant_id="reviewer",
    configurable={"repo": {"owner": "acme", "name": "widgets"}, "pr_number": 7},
)


@pytest.fixture(autouse=True)
def token_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "REMOTE_RUNTIME_TOKEN_SECRET",
        "current-signing-key-0123456789abcdef,previous-signing-key-0123456789abcdef",
    )


@pytest.mark.parametrize("fails", [False, True])
async def test_backend_operation_traces_keep_runtime_events_and_errors(fails: bool) -> None:
    from langchain_core.tracers.run_collector import RunCollectorCallbackHandler
    from langgraph.config import get_config, get_stream_writer
    from langgraph.runtime import Runtime

    from openswe.remote_runtime.runs import in_graph_context

    collector = RunCollectorCallbackHandler()
    events: list[object] = []

    async def operation(runtime: Runtime) -> str:
        assert runtime is not None
        assert get_config()["configurable"]["thread_id"] == "thread-1"
        assert get_config()["metadata"]["run_id"] == "run-1"
        get_stream_writer()({"status": "observed"})
        if fails:
            raise ValueError("hook failed")
        return "result"

    pending = in_graph_context(
        {
            "callbacks": [collector],
            "configurable": {"thread_id": "thread-1"},
            "metadata": {"run_id": "run-1"},
            "run_name": "after_model",
        },
        operation,
        events=events,
    )
    if fails:
        with pytest.raises(ValueError, match="hook failed"):
            await pending
    else:
        assert await pending == "result"
        assert events == [{"status": "observed"}]

    [root] = collector.traced_runs
    assert root.name == "after_model"
    assert root.extra["metadata"]["backend_operation"] == "after_model"
    assert root.extra["metadata"]["run_id"] == "run-1"
    [node] = root.child_runs
    assert node.name == "after_model"
    assert bool(root.error) is fails
    assert bool(node.error) is fails


@pytest.mark.parametrize("requested", ["anthropic:claude-opus-5-5", "openai:gpt-6.1-sol"])
def test_remote_model_switch_overrides_cached_route_and_effort(requested: str) -> None:
    from openswe.remote_runtime.graph import RunSession
    from openswe.sandboxes.tool_runtime import ModelChoice, SurfaceModels, ToolSurface
    from openswe.utils.model import provider_model_kwargs

    main = ModelChoice("openai:gpt-6.1-sol", {})
    session = RunSession(
        ToolSurface(models=SurfaceModels(main=main, subagent=main, use_gateway=False)),
        {},
        None,
        started_here=True,
    )
    choice = session.model_for(
        {
            "model_route": "default",
            "requested_model": requested,
            "requested_effort": "medium",
        }
    )

    assert choice.model_id == requested
    assert choice.kwargs == provider_model_kwargs(requested, "medium", max_tokens=64_000)


async def test_mda_calls_hide_the_tools_the_graph_middleware_hides_from_each_call() -> None:
    from collections.abc import Awaitable, Callable

    from langchain.agents.middleware.types import AgentMiddleware, ModelRequest, ModelResponse
    from langchain_core.tools import tool

    from openswe.remote_runtime.graph import RunSession
    from openswe.sandboxes.tool_runtime import ToolSurface

    @tool
    def plan(step: str) -> str:
        """Plan a step."""
        return step

    @tool
    def record(summary: str) -> str:
        """Record a summary."""
        return summary

    class HideRecordUntilAsked(AgentMiddleware):
        async def awrap_model_call(
            self,
            request: ModelRequest,
            handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
        ) -> ModelResponse:
            if not request.state.get("asked"):
                request = request.override(tools=[t for t in request.tools if t.name != "record"])
            return await handler(request)

    session = RunSession(
        ToolSurface(tools={"plan": plan, "record": record}, middleware=[HideRecordUntilAsked()]),
        {},
        None,
        started_here=True,
    )

    assert (await session.model_call({"messages": []}, None)).hidden_tools == ["record"]
    assert (await session.model_call({"messages": [], "asked": True}, None)).hidden_tools == []


@pytest.mark.parametrize("environment", ["preview", "staging", "production"])
async def test_remote_prompts_include_deployment_context(
    monkeypatch: pytest.MonkeyPatch, environment: str
) -> None:
    from langgraph.runtime import Runtime

    from openswe.middleware.prepare_run import BasePrepareRunMiddleware
    from openswe.remote_runtime.graph import RunSession
    from openswe.sandboxes.tool_runtime import (
        ModelChoice,
        SurfaceModels,
        SurfaceSubagent,
        ToolSurface,
    )

    monkeypatch.setenv("OPENSWE_ENV", environment)
    model = ModelChoice("openai:gpt-6.1-sol", {})
    session = RunSession(
        ToolSurface(
            middleware=[BasePrepareRunMiddleware()],
            models=SurfaceModels(main=model, subagent=model, use_gateway=False),
            subagents=[SurfaceSubagent("worker", "worker", "subagent instructions")],
        ),
        {},
        None,
        started_here=True,
    )
    main_prompt = (
        await session.model_call({"rendered_system_prompt": "main instructions"}, Runtime())
    ).system_prompt
    subagent_prompt = session.catalog().subagents[0].system_prompt

    for rendered, original in (
        (main_prompt, "main instructions"),
        (subagent_prompt, "subagent instructions"),
    ):
        if environment == "production":
            assert rendered == original
        else:
            assert rendered.count(f"**{environment}**") == 1
            assert rendered.endswith(original)


async def test_app_lifespan_exits_without_managed_deep_agents(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openswe.api.app import app, lifespan
    from openswe.remote_runtime.client import RemoteRuntimeConfigurationError

    monkeypatch.delenv("MDA_RUNTIME_URL", raising=False)

    with pytest.raises(RemoteRuntimeConfigurationError, match="MDA_RUNTIME_URL is required"):
        async with lifespan(app):
            pass


@pytest.mark.parametrize("first_call", ["phase", "operation"])
async def test_reviewer_tools_get_a_github_token_on_a_replica_that_did_not_prepare_the_run(
    monkeypatch: pytest.MonkeyPatch, first_call: str
) -> None:
    import openswe.reviewer as reviewer
    from openswe.github.thread_token import (
        invalidate_cached_github_token,
        resolve_thread_github_token,
    )
    from openswe.remote_runtime.graph import RunSession
    from openswe.sandboxes.tool_runtime import ToolSurface

    async def mint(*, repositories: list[str] | None = None) -> tuple[str, str | None]:
        assert repositories == ["widgets"]
        return "ghs_reviewer", None

    monkeypatch.setattr(reviewer, "get_github_app_installation_token_with_expiry", mint)
    await invalidate_cached_github_token(RUN.thread_id)
    config = {"configurable": {**RUN.configurable, "thread_id": RUN.thread_id, "source": "github"}}
    session = RunSession(
        ToolSurface(
            middleware=[
                reviewer.PrepareReviewerRunMiddleware(
                    thread_id=RUN.thread_id, config=config, use_gateway=False
                )
            ]
        ),
        config,
        None,
        started_here=False,
    )

    # An operation's call carries no run state, so only preparation rejoins for it.
    if first_call == "phase":
        await session.join({"messages": [], "run_prepared": True}, None)
    else:
        await session.rejoin_preparation(None)

    assert await resolve_thread_github_token(config) == "ghs_reviewer"


def test_run_token_round_trips_the_signed_run() -> None:
    assert verify_runtime_token(sign_runtime_token(RUN)) == RUN


def test_run_token_signed_with_a_rotated_out_key_still_verifies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("REMOTE_RUNTIME_TOKEN_SECRET", "previous-signing-key-0123456789abcdef")
    token = sign_runtime_token(RUN)
    monkeypatch.setenv(
        "REMOTE_RUNTIME_TOKEN_SECRET",
        "current-signing-key-0123456789abcdef,previous-signing-key-0123456789abcdef",
    )

    assert verify_runtime_token(token).thread_id == "thread-1"


@pytest.mark.parametrize(
    "token",
    [
        pytest.param(
            jwt.encode(
                {"sub": "thread-1", "cfg": {}},
                "current-signing-key-0123456789abcdef",
                algorithm="HS256",
            ),
            id="missing-claims",
        ),
        pytest.param(
            jwt.encode(
                {
                    "iss": "open-swe",
                    "aud": "open-swe-remote-runtime",
                    "iat": 0,
                    "exp": 1,
                    "sub": "thread-1",
                    "assistant_id": "reviewer",
                    "cfg": {},
                },
                "current-signing-key-0123456789abcdef",
                algorithm="HS256",
            ),
            id="expired",
        ),
        pytest.param(
            jwt.encode(
                {
                    "iss": "open-swe",
                    "aud": "open-swe-remote-runtime",
                    "iat": int(time.time()),
                    "exp": int(time.time()) + 60,
                    "sub": "thread-1",
                    "assistant_id": "reviewer",
                    "cfg": {},
                },
                "someone-elses-signing-key-0123456789abcdef",
                algorithm="HS256",
            ),
            id="foreign-key",
        ),
    ],
)
def test_run_token_refuses_tokens_this_backend_did_not_issue(token: str) -> None:
    with pytest.raises(RuntimeTokenError):
        verify_runtime_token(token)


async def _status(authorization: str | None) -> tuple[int, RemoteRun | None]:
    seen: list[RemoteRun] = []

    async def inner(scope: Scope, receive: Receive, send: Send) -> None:
        seen.append(scope["state"]["remote_run"])
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    headers = [(b"authorization", authorization.encode())] if authorization else []
    scope: Scope = {"type": "http", "method": "POST", "path": "/", "headers": headers}
    sent: list[Message] = []

    async def receive() -> Message:
        return {"type": "http.request", "body": b""}

    async def send(message: Message) -> None:
        sent.append(message)

    await RunTokenMiddleware(inner)(scope, receive, send)
    return sent[0]["status"], (seen[0] if seen else None)


async def test_tool_server_refuses_calls_without_a_valid_run_token() -> None:
    assert await _status(None) == (401, None)
    assert await _status("Bearer not-a-token") == (401, None)


async def test_tool_server_hands_the_verified_run_to_its_handlers() -> None:
    assert await _status(f"Bearer {sign_runtime_token(RUN)}") == (200, RUN)


async def test_mda_catalog_leaves_integrations_and_code_mode_to_mda(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Seldom-needed tools go to MDA's code mode, not the model's schemas; the graph's own stays here."""
    # A provider MDA declares no box for, so the catalog reads no thread metadata.
    monkeypatch.setenv("SANDBOX_TYPE", "local")
    from langchain.agents.middleware import ModelCallLimitMiddleware
    from langchain.agents.middleware.types import AgentMiddleware
    from langchain_core.tools import StructuredTool
    from langchain_quickjs import CodeInterpreterMiddleware

    from openswe.mcp.models import MCPToolProvenance
    from openswe.middleware.dynamic_tools import DynamicToolMiddleware
    from openswe.middleware.task_coordination import TaskCoordinationMiddleware
    from openswe.remote_runtime.graph import RemoteGraph
    from openswe.sandboxes.tool_runtime import (
        ModelChoice,
        SurfaceModels,
        SurfaceSubagent,
        ToolSurface,
    )

    def integration(name: str) -> StructuredTool:
        async def run(query: str) -> str:
            return query

        return StructuredTool.from_function(coroutine=run, name=name, description=name)

    async def build(config: object, surface: ToolSurface) -> None:
        search = integration("mcp_search")
        search.metadata = {
            "mcp_provenance": MCPToolProvenance(
                connection_name="lmt", scope="managed", overridden_scopes=["instance", "user"]
            ),
            "headers": {"Authorization": "private-token"},
            "namespace": ["managed_tools", "private-owner", "private-gateway"],
        }
        surface.dynamic = DynamicToolMiddleware({"MCP": [search, integration("mcp.dotted")]})
        core = [
            integration(name)
            for name in (
                "web_search",
                "manage_thread",
                "slack_reply",
                "message_task_thread",
                "load_integration_tools",
            )
        ]
        middleware: list[AgentMiddleware] = [
            surface.dynamic,
            CodeInterpreterMiddleware(ptc=[integration("mcp_search")]),
            TaskCoordinationMiddleware(RUN.thread_id, True, None),
            ModelCallLimitMiddleware(run_limit=20, exit_behavior="end"),
        ]
        surface.base_tools = core
        surface.code_mode = True
        surface.excluded = frozenset({"grep", "glob"})
        surface.middleware = middleware
        surface.subagents = [SurfaceSubagent("general-purpose", "helper", "instructions")]
        choice = ModelChoice("openai:gpt-6.1-sol", {})
        surface.models = SurfaceModels(main=choice, subagent=choice, use_gateway=False)

    async def start_models(cfg: object) -> object:
        raise AssertionError("not asked for")

    graph = RemoteGraph("agent", build, start_models)
    catalog = await graph.hook(RUN, "catalog", {"run_id": "run-1"})

    assert isinstance(catalog, dict)
    operations = catalog["operations"]
    assert isinstance(operations, list)
    names = {op["name"] for op in operations if isinstance(op, dict)}
    assert {"mcp_search", "mcp.dotted", "web_search", "manage_thread", "slack_reply"} <= names
    assert "message_task_thread" in names
    assert "eval" not in names
    excluded = catalog["excluded"]
    assert isinstance(excluded, list) and "load_integration_tools" in excluded
    # A coordinator launches async workers; their task tools track them, not deepagents'.
    assert "start_async_task" not in excluded
    assert {"check_async_task", "update_async_task", "cancel_async_task"} <= set(excluded)
    # And delegates only through them: no synchronous subagents, nor deepagents' default.
    assert catalog["subagents"] == []
    assert "task" in excluded
    search = next(op for op in operations if isinstance(op, dict) and op["name"] == "mcp_search")
    assert isinstance(search, dict)
    expected = {
        "connection_name": "lmt",
        "scope": "managed",
        "overridden_scopes": ["instance", "user"],
    }
    assert json.dumps(expected, separators=(",", ":")) in str(search["description"])
    assert "private-token" not in str(catalog)
    assert "private-owner" not in str(catalog)
    assert "private-gateway" not in str(catalog)
    assert catalog["code_only"] == ["manage_thread", "mcp_search"]
    # MDA runs the graph's cap on model calls, such as an incident policy's.
    assert catalog["model_call_limit"] == 20
    # Code mode calls the file and shell tools too, through MDA's own with their checks,
    # but never one the graph excludes.
    assert catalog["also_from_code"] == [
        "delete",
        "edit_file",
        "execute",
        "ls",
        "read_file",
        "web_search",
        "write_file",
    ]


async def test_an_operation_gets_the_state_it_reads_and_answers_the_state_it_changed() -> None:
    """An operation is its tool's work alone: what it reads comes with the call, what it writes goes back."""
    from typing import Annotated

    from langchain_core.messages import ToolMessage
    from langchain_core.tools import InjectedToolCallId, tool
    from langgraph.prebuilt import InjectedState
    from langgraph.types import Command
    from runtime_api.operations import RunOperation

    from openswe.remote_runtime.graph import RunSession
    from openswe.remote_runtime.operations import Injections
    from openswe.sandboxes.tool_runtime import ToolSurface

    @tool
    def switch(
        route: Annotated[str | None, InjectedState("model_route")],
        call_id: Annotated[str, InjectedToolCallId],
    ) -> Command:
        """Switch to the performance model."""
        return Command(
            update={
                "requested_model": f"after-{route}",
                "messages": [ToolMessage(content="switched", tool_call_id=call_id)],
            }
        )

    surface = ToolSurface(base_tools=[switch])
    await surface.prepare()
    session = RunSession(surface, {"configurable": {}}, None, started_here=True)

    reads = Injections.of(surface.tools["switch"])
    result = await session.operation(
        RunOperation(name="switch", state={"model_route": "fast"}, request_id="call-1")
    )

    assert reads.reads == ["model_route"]
    assert not reads.reads_all
    assert result.content == "switched"
    assert result.state == {"requested_model": "after-fast"}


async def test_the_files_api_reaches_the_threads_files_as_the_run() -> None:
    """A bridge checks who the run is for, which it reads from the run's own config."""
    from deepagents.backends.protocol import ExecuteResponse, SandboxBackendProtocol

    from openswe.remote_runtime.graph import API, RunSession
    from openswe.run_config import RunConfig
    from openswe.sandboxes.tool_runtime import ToolSurface

    seen: list[str | None] = []

    class Machine(SandboxBackendProtocol):
        @property
        def id(self) -> str:
            return "machine"

        async def aexecute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
            seen.append(RunConfig.from_runtime().thread_id)
            return ExecuteResponse(output=f"ran {command}", exit_code=0)

    session = RunSession(
        ToolSurface(files=Machine()),
        {"configurable": {"thread_id": "thread-1"}},
        None,
        started_here=True,
    )

    reply = await API.dispatch("files.execute", session, {"command": "ls"})

    assert reply == {"output": "ran ls", "exit_code": 0, "truncated": False}
    assert seen == ["thread-1"]


async def test_a_cancelled_operation_ends_the_call_rather_than_answering() -> None:
    from langchain_core.tools import tool
    from langgraph.errors import NodeCancelledError
    from runtime_api.operations import RunOperation

    from openswe.remote_runtime.graph import RunSession
    from openswe.sandboxes.tool_runtime import ToolSurface

    @tool
    async def wait() -> str:
        """Wait for something."""
        raise NodeCancelledError("tools")

    surface = ToolSurface(base_tools=[wait])
    await surface.prepare()
    session = RunSession(surface, {"configurable": {}}, None, started_here=True)

    with pytest.raises(NodeCancelledError):
        await session.operation(RunOperation(name="wait", request_id="call-1"))


async def test_an_operation_that_runs_a_command_is_put_to_the_shell_guards_first() -> None:
    from langchain_core.tools import tool
    from runtime_api.operations import RunOperation

    from openswe.middleware.pr_creation_guard import PullRequestCreationGuardMiddleware
    from openswe.remote_runtime.graph import RunSession
    from openswe.sandboxes.tool_runtime import ToolSurface

    ran: list[str] = []

    @tool
    def background_execute(command: str) -> str:
        """Run a command in the background."""
        ran.append(command)
        return "started"

    surface = ToolSurface(
        base_tools=[background_execute], shell_guards=[PullRequestCreationGuardMiddleware()]
    )
    await surface.prepare()
    session = RunSession(surface, {"configurable": {}}, None, started_here=True)

    refused = await session.operation(
        RunOperation(
            name="background_execute",
            arguments={"command": "gh pr create --fill"},
            request_id="call-1",
        )
    )
    started = await session.operation(
        RunOperation(
            name="background_execute", arguments={"command": "make test"}, request_id="call-2"
        )
    )

    assert refused.status == "error"
    assert "pr_creation_fallback_blocked" in str(refused.content)
    assert started.content == "started"
    assert ran == ["make test"]


async def _hook_status(
    monkeypatch: pytest.MonkeyPatch, token: str, hook: str
) -> tuple[int, list[tuple[str, str]]]:
    """Call ``hook`` with ``token`` and report the status and what served it, as whom."""
    from openswe.remote_runtime import server

    served: list[tuple[str, str]] = []

    async def graph(run: RemoteRun, called: str, body: object) -> dict[str, object]:
        if called not in {"catalog", "prepare", "before_model", "call", "after_agent"}:
            raise server.UnknownHookError(called)
        served.append((called, run.thread_id))
        return {}

    async def adopt(grant: WorkerGrant, body: object) -> dict[str, object]:
        served.append(("adopt", grant.coordinator.thread_id))
        return {}

    monkeypatch.setattr(server, "_graphs", lambda: {"agent": graph})
    monkeypatch.setattr(server, "_grant_hooks", lambda: {"adopt": adopt})
    scope: Scope = {
        "type": "http",
        "method": "POST",
        "path": f"/remote-runtime/hooks/{hook}",
        "path_params": {"hook": hook},
        "headers": [(b"authorization", f"Bearer {token}".encode())],
    }
    sent: list[Message] = []

    async def receive() -> Message:
        return {"type": "http.request", "body": b"{}"}

    async def send(message: Message) -> None:
        sent.append(message)

    await server.HOOKS(scope, receive, send)
    return sent[0]["status"], served


async def test_a_worker_grant_reads_its_coordinators_catalog_and_adopts_nothing_else(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    coordinator = RemoteRun(thread_id="coordinator", assistant_id="agent", configurable={})
    grant = sign_worker_grant(WorkerGrant(coordinator=coordinator, request_id="call-1"))
    with pytest.raises(RuntimeTokenError):
        verify_runtime_token(grant)
    with pytest.raises(RuntimeTokenError):
        verify_worker_grant(sign_runtime_token(coordinator))

    assert await _hook_status(monkeypatch, grant, "catalog") == (
        200,
        [("catalog", "coordinator")],
    )
    assert await _hook_status(monkeypatch, grant, "adopt") == (200, [("adopt", "coordinator")])
    for hook in ("prepare", "before_model", "call", "after_agent", "launch_worker"):
        assert await _hook_status(monkeypatch, grant, hook) == (403, [])
    # A run token never adopts: only a launched worker's grant does.
    status, _ = await _hook_status(monkeypatch, sign_runtime_token(coordinator), "adopt")
    assert status == 404


async def test_an_adopted_worker_acts_as_its_own_thread_never_its_coordinator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from unittest.mock import AsyncMock, MagicMock

    from runtime_api.catalog import ModelSpec, RunModels

    from openswe.prompts import prompt
    from openswe.remote_runtime import async_workers, hosted

    coordinator = RemoteRun(
        thread_id="coordinator", assistant_id="agent", configurable={"github_login": "owner"}
    )
    grant = WorkerGrant(coordinator=coordinator, request_id="call-1")
    register = AsyncMock(return_value={"success": True})
    monkeypatch.setattr(async_workers.service, "register_async_worker", register)
    monkeypatch.setattr(
        async_workers.service,
        "recipient_config",
        AsyncMock(return_value={"thread_id": "worker", "github_login": "owner"}),
    )
    monkeypatch.setattr(async_workers.service.Actor, "resolve", AsyncMock(return_value="actor"))
    spec = ModelSpec(model_id="openai:gpt-6.1-sol", kwargs={})
    graph = MagicMock(
        start_models=AsyncMock(
            return_value=RunModels(model=spec, subagent_model=spec, use_gateway=False)
        )
    )
    monkeypatch.setattr(hosted, "hosted_graphs", lambda: {"agent": graph})

    # The worker's first message: the assignment its launch rendered.
    assignment = prompt(
        "tasks/assignment", coordinator_thread_id="coordinator", instructions="Fix it"
    )
    reply = await async_workers.adopt(
        grant, {"thread_id": "worker", "run_id": "run-1", "instructions": assignment}
    )

    assert isinstance(reply, dict)
    token = reply["run_token"]
    assert isinstance(token, str)
    adopted = verify_runtime_token(token)
    assert adopted.thread_id == "worker"
    assert adopted.configurable["thread_id"] == "worker"
    register.assert_awaited_once_with("actor", "call-1", "worker", "Fix it")


async def test_a_replayed_launch_reports_the_worker_it_registered_and_stops_the_other(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from unittest.mock import AsyncMock, MagicMock
    from uuid import uuid7

    from openswe.remote_runtime import async_workers
    from openswe.tasks import store
    from openswe.threads import runtimes

    coordinator = RemoteRun(thread_id="coordinator", assistant_id="agent", configurable={})
    task = store.Task(title="Fix login", workspace_id=uuid7(), coordinator_thread_id="coordinator")
    first = store.TaskDelegation(
        worker_thread_id="first-worker",
        task_id=task.id,
        coordinator_thread_id="coordinator",
        instructions="Fix login",
        model="openai:gpt-6.1-sol",
        effort="low",
    )
    monkeypatch.setattr(
        async_workers.service,
        "register_async_worker",
        AsyncMock(side_effect=store.WorkerAlreadyLaunchedError(task, first)),
    )
    monkeypatch.setattr(async_workers.service.Actor, "resolve", AsyncMock(return_value="actor"))
    client = MagicMock()
    client.runs.cancel = AsyncMock()
    mda = MagicMock(client=client)
    monkeypatch.setattr(runtimes.Runtimes, "mda", property(lambda self: mda))

    reply = await async_workers.launched(
        coordinator,
        {
            "tool_call_id": "call-1",
            "instructions": "Fix login",
            "worker_thread_id": "second-worker",
            "run_id": "run-2",
        },
    )

    assert isinstance(reply, dict)
    worker = reply["worker"]
    assert isinstance(worker, dict)
    assert worker["worker_thread_id"] == "first-worker"
    client.runs.cancel.assert_awaited_once_with("second-worker", "run-2")


async def test_a_launched_run_that_ended_before_its_worker_was_registered_is_settled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from unittest.mock import AsyncMock, MagicMock

    from openswe import completion
    from openswe.remote_runtime import async_workers
    from openswe.threads import runtimes

    coordinator = RemoteRun(thread_id="coordinator", assistant_id="agent", configurable={})
    monkeypatch.setattr(
        async_workers.service,
        "register_async_worker",
        AsyncMock(return_value={"worker_thread_id": "worker"}),
    )
    monkeypatch.setattr(async_workers.service.Actor, "resolve", AsyncMock(return_value="actor"))
    client = MagicMock()
    mda = MagicMock(client=client)
    monkeypatch.setattr(runtimes.Runtimes, "mda", property(lambda self: mda))
    settle = AsyncMock()
    monkeypatch.setattr(completion, "handle_run_completion", settle)
    body = {
        "tool_call_id": "call-1",
        "instructions": "Fix login",
        "worker_thread_id": "worker",
        "run_id": "run-1",
    }

    # Still running: its webhook reports it, to a registered worker.
    client.runs.get = AsyncMock(return_value={"run_id": "run-1", "status": "running"})
    await async_workers.launched(coordinator, body)
    settle.assert_not_awaited()

    # Its webhook came before the worker existed, so registering settles it.
    client.runs.get = AsyncMock(
        return_value={"run_id": "run-1", "status": "error", "error": "boom"}
    )
    await async_workers.launched(coordinator, body)
    settle.assert_awaited_once_with(
        {
            "thread_id": "worker",
            "run_id": "run-1",
            "status": "error",
            "metadata": None,
            "error": "boom",
        }
    )
