import asyncio
import json
import logging
import time
from collections.abc import Mapping

import jwt
import pytest
from pydantic import JsonValue
from starlette.types import Message, Receive, Scope, Send

from openswe.agent_api import server
from openswe.agent_api.server import RunTokenMiddleware
from openswe.agent_api.tokens import (
    RemoteRun,
    RunSettings,
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
    expires_at=int(time.time()) + 3600,
)


def _asgi_receive(body: bytes) -> Receive:
    """Like an ASGI server: the body once, then nothing until the client disconnects."""
    messages: asyncio.Queue[Message] = asyncio.Queue()
    messages.put_nowait({"type": "http.request", "body": body, "more_body": False})
    return messages.get


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

    from openswe.agent_api.runs import in_graph_context

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
    from runtime_api.catalog import ModelSpec, RunModels

    from openswe.agent_api.loop import ModelChoices
    from openswe.utils.model import provider_model_kwargs

    main = ModelSpec(model_id="openai:gpt-6.1-sol", kwargs={})
    choices = ModelChoices(RunModels(model=main, subagent_model=main, use_gateway=False))
    choice = choices.model_for(
        {
            "model_route": "default",
            "requested_model": requested,
            "requested_effort": "medium",
        }
    )

    assert choice.model_id == requested
    assert choice.kwargs == provider_model_kwargs(requested, "medium", max_tokens=64_000)


async def test_the_named_calls_around_a_model_call_never_plan_the_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from unittest.mock import AsyncMock

    from langchain_core.messages import HumanMessage, messages_to_dict

    from openswe.agent_api import agent
    from openswe.agent_api import graph as graph_module
    from openswe.agent_api.graph import RemoteGraph
    from openswe.middleware.model_selection import RouteSelection
    from openswe.middleware.task_coordination import TaskCoordinationMiddleware
    from openswe.sandboxes.tool_runtime import ToolSurface
    from openswe.utils.model import provider_model_kwargs

    async def never_planned(config: object, surface: ToolSurface) -> None:
        raise AssertionError("A call around a model call never plans the run")

    classify = AsyncMock(return_value=RouteSelection("fast"))
    refresh = AsyncMock(return_value=None)
    monkeypatch.setattr("openswe.middleware.model_selection._select_jev_route", classify)
    monkeypatch.setattr(graph_module, "refresh_thread_github_proxy", refresh)
    monkeypatch.setattr(TaskCoordinationMiddleware, "for_thread", AsyncMock(return_value=None))
    graph = RemoteGraph("agent", never_planned, agent.AgentParts())
    run = RUN.model_copy(update={"assistant_id": "agent"})
    asked = messages_to_dict([HumanMessage(content="Fix the login", id="human-1")])

    route = await graph.api(run, "route_model", {"run_id": "run-1", "humans": asked})
    chosen = await graph.api(
        run,
        "model_choice",
        {"run_id": "run-1", "model_id": "anthropic:claude-opus-5-5", "effort": "high"},
    )
    context = await graph.api(run, "task_context", {"run_id": "run-1"})
    refreshed = await graph.api(run, "refresh_github_proxy", {"run_id": "run-1"})

    assert route == {"route": "fast", "persist": True}
    classify.assert_awaited_once_with("Fix the login")
    assert isinstance(chosen, dict)
    assert chosen["model_id"] == "anthropic:claude-opus-5-5"
    assert chosen["kwargs"] == provider_model_kwargs(
        "anthropic:claude-opus-5-5", "high", max_tokens=64_000
    )
    # A thread in no task gets no task context.
    assert context == {"instructions": None, "message": None}
    assert refreshed == {"expires_at": None}
    refresh.assert_awaited_once_with(run.thread_id, workspace_slug=None)


@pytest.mark.usefixtures("registry_db")
async def test_a_report_is_recorded_once_however_often_it_arrives(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A batch stops at the first report that fails, which its next delivery records,
    and one delivered again after its answer was lost is not recorded twice."""
    from unittest.mock import AsyncMock

    from runtime_api.loop import SETTLE_REVIEW_CHECK, STEP_LIMIT_REACHED
    from runtime_api.operations import RunRequest
    from runtime_api.reports import Reports, ReportsTaken, item

    from openswe.agent_api import agent
    from openswe.agent_api import graph as graph_module
    from openswe.agent_api.graph import RemoteGraph
    from openswe.sandboxes.tool_runtime import ToolSurface

    async def never_planned(config: object, surface: ToolSurface) -> None:
        raise AssertionError("A report never plans the run")

    told = AsyncMock(side_effect=[RuntimeError("Slack is down"), None])
    settled = AsyncMock()
    monkeypatch.setattr(graph_module, "notify_step_limit", told)
    monkeypatch.setattr(graph_module, "settle_review_check", settled)
    graph = RemoteGraph("agent", never_planned, agent.AgentParts())
    run = RUN.model_copy(update={"assistant_id": "agent"})
    batch = Reports(
        items=[
            item(endpoint, RunRequest(run_id="run-1"), key=f"run-1:{endpoint.name}", trace={})
            for endpoint in (STEP_LIMIT_REACHED, SETTLE_REVIEW_CHECK)
        ]
    ).model_dump(mode="json")

    async def deliver() -> ReportsTaken:
        return ReportsTaken.model_validate(await graph.api(run, "reports", batch))

    first = await deliver()
    assert first.replies == []
    assert first.refused == "RuntimeError: Slack is down"
    # Nothing after a report that failed is tried, so none lands before it.
    settled.assert_not_awaited()

    second = await deliver()
    lost_answer = await deliver()

    assert second == lost_answer == ReportsTaken(replies=[{}, {}])
    assert told.await_count == 2
    settled.assert_awaited_once()


@pytest.mark.usefixtures("registry_db")
async def test_a_report_whose_reply_was_not_kept_holds_up_nothing_behind_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The report is answered and its key given up, so a later delivery records it
    rather than waiting out another delivery's claim."""
    from unittest.mock import AsyncMock

    from runtime_api.loop import SETTLE_REVIEW_CHECK
    from runtime_api.operations import RunRequest
    from runtime_api.reports import Reports, ReportsTaken, item

    from openswe.agent_api import agent
    from openswe.agent_api import graph as graph_module
    from openswe.agent_api.graph import RemoteGraph
    from openswe.agent_api.report_receipts import ReportReceipts
    from openswe.sandboxes.tool_runtime import ToolSurface

    async def never_planned(config: object, surface: ToolSurface) -> None:
        raise AssertionError("A report never plans the run")

    settled = AsyncMock()
    monkeypatch.setattr(graph_module, "settle_review_check", settled)
    kept = ReportReceipts.record
    monkeypatch.setattr(
        ReportReceipts, "record", AsyncMock(side_effect=[RuntimeError("database went away")])
    )
    graph = RemoteGraph("agent", never_planned, agent.AgentParts())
    run = RUN.model_copy(update={"assistant_id": "agent"})
    batch = Reports(
        items=[item(SETTLE_REVIEW_CHECK, RunRequest(run_id="run-1"), key="run-1:settle", trace={})]
    ).model_dump(mode="json")

    async def deliver() -> ReportsTaken:
        return ReportsTaken.model_validate(await graph.api(run, "reports", batch))

    assert await deliver() == ReportsTaken(replies=[{}])
    monkeypatch.setattr(ReportReceipts, "record", kept)
    assert await deliver() == ReportsTaken(replies=[{}])
    assert await deliver() == ReportsTaken(replies=[{}])

    # Done again only by the delivery after the reply was lost, then never again.
    assert settled.await_count == 2


async def test_a_runs_tool_and_shell_calls_never_plan_it_and_read_its_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from unittest.mock import AsyncMock

    from langchain_core.tools import tool
    from langgraph.config import get_config

    from openswe.agent_api import agent
    from openswe.agent_api.graph import RemoteGraph
    from openswe.sandboxes.tool_runtime import ToolSurface

    @tool
    def draft_preference(note: str) -> str:
        """Say whether this run opens pull requests as drafts."""
        return f"{note}: {get_config()['configurable'].get('draft_prs')}"

    async def never_planned(config: object, surface: ToolSurface) -> None:
        raise AssertionError("A tool or shell call never plans the run")

    monkeypatch.setattr(
        agent, "agent_tools_by_name", lambda: {draft_preference.name: draft_preference}
    )
    monkeypatch.setattr(agent, "resolve_github_token", AsyncMock())
    graph = RemoteGraph("agent", never_planned, agent.AgentParts())
    # The settings decided as the run started, signed into its token.
    run = RUN.model_copy(
        update={"assistant_id": "agent", "settings": RunSettings(draft_prs=False)}
    ).granting()
    call = {
        "run_id": "run-1",
        "name": "draft_preference",
        "arguments": {"note": "drafts"},
        "request_id": "call-1",
    }

    result = await graph.api(run, "operation", {**call, "grant": run.grant("draft_preference")})
    # Only an operation the run's catalog granted it runs, however the call names it.
    ungranted = await graph.api(run, "operation", call)
    regranted = await graph.api(
        run, "operation", {**call, "grant": run.granting().grant("draft_preference")}
    )
    verdict = await graph.api(
        run, "check_shell", {"run_id": "run-1", "tool": "execute", "command": "ls"}
    )

    assert isinstance(result, dict) and result["content"] == "drafts: False"
    assert verdict == {"allowed": True, "command": None, "refusal": None}
    for refused in (ungranted, regranted):
        assert isinstance(refused, dict) and refused["status"] == "error"
        assert "not one of this run's operations" in str(refused["content"])

    # A token from before operations were granted reaches only the tools its plan offers.
    async def offers_nothing(config: object, surface: ToolSurface) -> None:
        return None

    planned = RemoteGraph("agent", offers_nothing, agent.AgentParts())
    unplanned = await planned.api(
        RUN.model_copy(update={"assistant_id": "agent"}), "operation", call
    )
    assert isinstance(unplanned, dict) and unplanned["status"] == "error"
    assert "drafts" not in str(unplanned["content"])


async def test_mda_calls_hide_the_tools_the_graph_middleware_hides_from_each_call() -> None:
    from langchain.agents.middleware.types import AgentMiddleware
    from langchain_core.tools import tool
    from langgraph.runtime import Runtime
    from runtime_api.catalog import ModelSpec, RunModels

    from openswe.agent_api.loop import RunLoop
    from openswe.middleware.model_call_shape import CallShape

    @tool
    def plan(step: str) -> str:
        """Plan a step."""
        return step

    @tool
    def record(summary: str) -> str:
        """Record a summary."""
        return summary

    class HideRecordUntilAsked(AgentMiddleware):
        async def ashape_model_call(
            self, call: CallShape, state: Mapping[str, object], runtime: Runtime
        ) -> CallShape:
            return call if state.get("asked") else call.hiding(["record"])

    model = ModelSpec(model_id="openai:gpt-6.1-sol", kwargs={})
    loop = RunLoop(
        [HideRecordUntilAsked()],
        RunModels(model=model, subagent_model=model, use_gateway=False),
        offered={plan.name, record.name},
    )

    assert (await loop.model_call({"messages": []}, Runtime())).hidden_tools == ["record"]
    assert (await loop.model_call({"messages": [], "asked": True}, Runtime())).hidden_tools == []


@pytest.mark.parametrize("environment", ["preview", "staging", "production"])
async def test_remote_prompts_include_deployment_context(
    monkeypatch: pytest.MonkeyPatch, environment: str
) -> None:
    from langgraph.runtime import Runtime

    from openswe.agent_api.graph import RunSession
    from openswe.middleware.prepare_run import BasePrepareRunMiddleware
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
        await session.loop.model_call({"rendered_system_prompt": "main instructions"}, Runtime())
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
    from openswe.agent_api.client import RemoteRuntimeConfigurationError
    from openswe.api.app import app, lifespan

    monkeypatch.delenv("MDA_RUNTIME_URL", raising=False)

    with pytest.raises(RemoteRuntimeConfigurationError, match="MDA_RUNTIME_URL is required"):
        async with lifespan(app):
            pass


@pytest.mark.parametrize("first_call", ["phase", "operation"])
async def test_reviewer_tools_get_a_github_token_on_a_replica_that_did_not_prepare_the_run(
    monkeypatch: pytest.MonkeyPatch, first_call: str
) -> None:
    import openswe.reviewer as reviewer
    from openswe.agent_api.graph import RunSession
    from openswe.github.thread_token import (
        invalidate_cached_github_token,
        resolve_thread_github_token,
    )
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


@pytest.mark.parametrize("additional", [True, {"type": "string"}, False, None])
async def test_operation_respects_open_tool_argument_schemas(additional: JsonValue) -> None:
    from langchain_core.tools import StructuredTool
    from runtime_api.operations import RunOperation

    from openswe.agent_api.graph import RunSession
    from openswe.sandboxes.tool_runtime import ToolSurface

    schema: dict[str, JsonValue] = {
        "type": "object",
        "properties": {"repo": {"type": "string"}, "limit": {"type": "integer"}},
        "required": ["repo"],
    }
    if additional is not None:
        schema["additionalProperties"] = additional

    async def homebase(**arguments: JsonValue) -> str:
        return json.dumps(arguments)

    tool = StructuredTool.from_function(
        coroutine=homebase, name="homebase", description="Homebase tool", args_schema=schema
    )
    session = RunSession(ToolSurface(tools={tool.name: tool}), {}, None, started_here=True)
    arguments: dict[str, JsonValue] = {"repo": "open-swe", "commit": "abc123"}

    result = await session.operation(
        RunOperation(name=tool.name, arguments=arguments, request_id="call-1")
    )

    if additional is True or isinstance(additional, dict):
        assert result.status == "success"
        assert isinstance(result.content, str)
        assert json.loads(result.content) == arguments
    else:
        assert result.status == "error"
        assert result.content == (
            "Unexpected arguments for homebase: commit. "
            "Accepted parameters: limit, repo* (* = required)"
        )


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


async def test_hook_failure_log_names_the_mda_run(monkeypatch: pytest.MonkeyPatch) -> None:
    from langgraph_api.logging import Formatter

    async def failing_hook(run: RemoteRun, hook: str, body: Mapping[str, JsonValue]) -> JsonValue:
        raise RuntimeError("connection pool exhausted")

    monkeypatch.setattr(server, "_graphs", lambda: {RUN.assistant_id: failing_hook})
    formatter = Formatter(None, None, "%")
    lines: list[str] = []

    class Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            lines.append(formatter.format(record))

    scope: Scope = {
        "type": "http",
        "method": "POST",
        "path": "/agent-api/before_model",
        "headers": [],
        "path_params": {"endpoint": "before_model"},
        "state": {"remote_run": RUN},
    }

    receive = _asgi_receive(json.dumps({"run_id": "mda-run-1"}).encode())

    async def send(message: Message) -> None:
        pass

    handler = Capture()
    server.logger.addHandler(handler)
    try:
        await server.api_endpoint(scope, receive, send)
    finally:
        server.logger.removeHandler(handler)

    assert len(lines) == 1
    assert all(value in lines[0] for value in ("mda-run-1", "thread-1", "before_model"))


async def test_mda_catalog_leaves_integrations_and_code_mode_to_mda(
    monkeypatch: pytest.MonkeyPatch, registry_db: None
) -> None:
    """Seldom-needed tools go to MDA's code mode, not the model's schemas; the graph's own stays here.

    Integrations stay out of the catalog: the backend keeps them and finds them for
    the run's ``search_tools`` and code mode.
    """
    from unittest.mock import AsyncMock

    from openswe.sandboxes.managed import ManagedSandbox

    # A thread with no box of MDA's, so the catalog reads no thread metadata.
    monkeypatch.setattr(ManagedSandbox, "for_thread", AsyncMock(return_value=None))
    monkeypatch.setenv("LANGCHAIN_REVISION_ID", "current-backend-sha")
    from langchain.agents.middleware import ModelCallLimitMiddleware
    from langchain.agents.middleware.types import AgentMiddleware
    from langchain_core.runnables import RunnableConfig
    from langchain_core.tools import StructuredTool
    from langchain_quickjs import CodeInterpreterMiddleware

    from openswe.agent_api.graph import RemoteGraph
    from openswe.mcp.models import MCPToolProvenance
    from openswe.middleware.dynamic_tools import DynamicToolMiddleware
    from openswe.middleware.task_coordination import TaskCoordinationMiddleware
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

    async def build(config: RunnableConfig, surface: ToolSurface) -> None:
        # What the planner decides for the run, such as the sender's draft preference.
        config["configurable"]["draft_prs"] = False
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

    graph = RemoteGraph("agent", build)
    run = RUN.granting()
    started = await graph.start_run(run)
    catalog = started.catalog.model_dump(mode="json")

    assert isinstance(catalog, dict)
    metadata = catalog["trace_metadata"]
    assert isinstance(metadata, dict)
    assert metadata["LANGSMITH_AGENT_VERSION"] == "current-backend-sha"
    operations = catalog["operations"]
    assert isinstance(operations, list)
    names = {op["name"] for op in operations if isinstance(op, dict)}
    assert {"mcp.dotted", "web_search", "manage_thread", "slack_reply"} <= names
    assert "message_task_thread" in names
    # An integration called only from code: kept on the backend.
    assert "mcp_search" not in names
    assert "eval" not in names
    excluded = catalog["excluded"]
    assert isinstance(excluded, list) and "load_integration_tools" in excluded
    # A coordinator launches async workers; their task tools track them, not deepagents'.
    assert "start_async_task" not in excluded
    assert {"check_async_task", "update_async_task", "cancel_async_task"} <= set(excluded)
    # And delegates only through them: no synchronous subagents, nor deepagents' default.
    assert catalog["subagents"] == []
    assert "task" in excluded
    assert "private-token" not in str(catalog)
    assert "private-owner" not in str(catalog)
    assert "private-gateway" not in str(catalog)
    assert catalog["code_only"] == ["manage_thread"]
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
        "search_tools",
        "web_search",
        "write_file",
    ]
    assert catalog["search_tools"] is True
    # MDA's search_tools reads as the backend describes it, with how to call what it finds.
    from openswe.prompts import prompt

    assert catalog["search_tools_description"] == prompt("tools/search_tools")
    # What planning decided for the run rides in its settings, and each operation is
    # granted to the run under its grant id.
    assert started.settings.draft_prs is False
    assert all(
        isinstance(op, dict) and run.granted(str(op["name"]), str(op["grant"])) for op in operations
    )

    # The run's context names its operations; it reads their specs and grants by its token.
    from runtime_api.tool_search import ResolvedTool, ToolMatches, ToolSpecs

    started_run = run.model_copy(update={"settings": started.settings})
    names = [str(op["name"]) for op in operations if isinstance(op, dict)]
    listed = ToolSpecs.model_validate(await graph.api(started_run, "tool_specs", {"names": names}))
    assert sorted(spec.name for spec in listed.tools) == sorted(names)
    assert all(run.granted(spec.name, spec.grant) for spec in listed.tools)
    # The run's search and code mode reach what its catalog leaves out, by its token.
    found = ToolMatches.model_validate(
        await graph.api(started_run, "search_tools", {"query": "mcp search", "limit": 1})
    )
    [match] = found.matches
    assert (match.name, match.code_name) == ("mcp_search", "mcpSearch")
    expected = {
        "connection_name": "lmt",
        "scope": "managed",
        "overridden_scopes": ["instance", "user"],
    }
    assert json.dumps(expected, separators=(",", ":")) in match.description
    looked_up = ResolvedTool.model_validate(
        await graph.api(started_run, "resolve_tool", {"name": "mcpSearch"})
    )
    assert looked_up.tool is not None and looked_up.tool.name == "mcp_search"
    assert run.granted("mcp_search", looked_up.tool.grant)
    # Called directly, by its own name, it is the same operation.
    by_name = ResolvedTool.model_validate(
        await graph.api(started_run, "resolve_tool", {"name": "mcp_search"})
    )
    assert by_name.tool == looked_up.tool
    unknown = await graph.api(started_run, "resolve_tool", {"name": "load_integration_tools"})
    # What the graph excludes is never found, so the run cannot call it either.
    assert ResolvedTool.model_validate(unknown).tool is None


def test_search_tools_ranks_by_bm25_and_names_code_calls() -> None:
    from runtime_api.catalog import ToolSpec

    from openswe.agent_api.tool_sets import ToolSet

    def tool(name: str, description: str, code_name: str | None) -> ToolSpec:
        return ToolSpec(
            name=name,
            description=description,
            parameters={"type": "object", "properties": {"query": {"type": "string"}}},
            code_name=code_name,
        )

    tools = ToolSet(
        [
            tool("searchDatadogLogs", "Search raw Datadog log entries.", "searchDatadogLogs"),
            tool("get_datadog_metric", "Query metrics from Datadog.", "getDatadogMetric"),
            tool("save_issue", "Create or update a Linear issue.", "saveIssue"),
            tool("slack_reply", "Reply in Slack.", None),
        ]
    )

    matches = tools.search("datadog logs", 2)

    assert [spec.name for spec in matches] == ["searchDatadogLogs", "get_datadog_metric"]
    assert tools.search("kubernetes", 5) == []


async def test_an_operation_gets_the_state_it_reads_and_answers_the_state_it_changed() -> None:
    """An operation is its tool's work alone: what it reads comes with the call, what it writes goes back."""
    from typing import Annotated

    from langchain_core.messages import ToolMessage
    from langchain_core.tools import InjectedToolCallId, tool
    from langgraph.prebuilt import InjectedState
    from langgraph.types import Command
    from runtime_api.operations import RunOperation

    from openswe.agent_api.graph import RunSession
    from openswe.agent_api.operations import Injections
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

    from openswe.agent_api.graph import API, RunSession
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

    from openswe.agent_api.graph import RunSession
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

    from openswe.agent_api.graph import RunSession
    from openswe.middleware.pr_creation_guard import PullRequestCreationGuardMiddleware
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


async def _call_status(
    monkeypatch: pytest.MonkeyPatch, token: str, endpoint: str
) -> tuple[int, list[tuple[str, str]]]:
    """Call ``endpoint`` with ``token`` and report the status and what served it, as whom."""
    from runtime_api.catalog import Catalog
    from runtime_api.endpoint import Handlers, UnknownEndpointError
    from runtime_api.workers import ADOPT, Adopt, Adopted

    from openswe.agent_api import async_workers, server

    served: list[tuple[str, str]] = []

    async def graph(run: RemoteRun, called: str, body: object) -> dict[str, object]:
        if called not in {"prepare", "before_model", "after_agent"}:
            raise UnknownEndpointError(called)
        served.append((called, run.thread_id))
        return {}

    async def adopt(grant: WorkerGrant, body: Adopt) -> Adopted:
        served.append(("adopt", grant.coordinator.thread_id))
        return Adopted(run_token="worker-token", catalog=Catalog())

    grants = Handlers[WorkerGrant]()
    grants.serve(ADOPT, adopt)
    monkeypatch.setattr(server, "_graphs", lambda: {"agent": graph})
    monkeypatch.setattr(async_workers, "GRANT_API", grants)
    scope: Scope = {
        "type": "http",
        "method": "POST",
        "path": f"/agent-api/{endpoint}",
        "path_params": {"endpoint": endpoint},
        "headers": [(b"authorization", f"Bearer {token}".encode())],
    }
    sent: list[Message] = []

    receive = _asgi_receive(
        b'{"thread_id": "worker", "instructions": "Fix it"}' if endpoint == "adopt" else b"{}"
    )

    async def send(message: Message) -> None:
        sent.append(message)

    await server.API(scope, receive, send)
    return sent[0]["status"], served


async def test_a_call_nests_under_the_runtime_span_that_made_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from unittest.mock import MagicMock

    from langchain_core.tracers.langchain import LangChainTracer, wait_for_all_tracers
    from langgraph.runtime import Runtime
    from langsmith import Client
    from langsmith.run_trees import RunTree

    from openswe.agent_api import server
    from openswe.agent_api.runs import in_graph_context

    client = MagicMock(spec=Client)
    tracer = LangChainTracer(client=client, project_name="backend")

    async def graph(run: RemoteRun, called: str, body: object) -> dict[str, object]:
        async def operation(runtime: Runtime) -> dict[str, object]:  # noqa: ARG001
            return {}

        return await in_graph_context({"callbacks": [tracer], "run_name": called}, operation)

    monkeypatch.setattr(server, "_graphs", lambda: {"agent": graph})
    caller = RunTree(name="model", run_type="chain", project_name="turns")
    headers = caller.to_headers()
    token = sign_runtime_token(RemoteRun(thread_id="t", assistant_id="agent", configurable={}))
    scope: Scope = {
        "type": "http",
        "method": "POST",
        "path": "/agent-api/before_model",
        "path_params": {"endpoint": "before_model"},
        "headers": [
            (b"authorization", f"Bearer {token}".encode()),
            (b"langsmith-trace", headers["langsmith-trace"].encode()),
            (b"baggage", headers["baggage"].encode()),
        ],
    }
    sent: list[Message] = []

    async def send(message: Message) -> None:
        sent.append(message)

    await server.API(scope, _asgi_receive(b"{}"), send)

    wait_for_all_tracers()
    assert sent[0]["status"] == 200
    [hook] = [
        run.kwargs
        for run in client.create_run.call_args_list
        if run.kwargs["parent_run_id"] is None or run.kwargs["parent_run_id"] == caller.id
    ]
    assert hook["name"] == "before_model"
    assert hook["parent_run_id"] == caller.id
    assert hook["trace_id"] == caller.trace_id
    assert hook["session_name"] == "turns"


async def test_a_worker_grant_only_adopts_a_run_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    coordinator = RemoteRun(thread_id="coordinator", assistant_id="agent", configurable={})
    grant = sign_worker_grant(WorkerGrant(coordinator=coordinator, request_id="call-1"))
    with pytest.raises(RuntimeTokenError):
        verify_runtime_token(grant)
    with pytest.raises(RuntimeTokenError):
        verify_worker_grant(sign_runtime_token(coordinator))

    assert await _call_status(monkeypatch, grant, "adopt") == (200, [("adopt", "coordinator")])
    for endpoint in (
        "catalog",
        "prepare",
        "before_model",
        "operation",
        "after_agent",
        "launch_worker",
    ):
        assert await _call_status(monkeypatch, grant, endpoint) == (403, [])
    # A run token never adopts: only a launched worker's grant does.
    status, _ = await _call_status(monkeypatch, sign_runtime_token(coordinator), "adopt")
    assert status == 404


async def test_only_a_body_unlike_its_request_is_refused_and_a_failure_inside_is_logged(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    from pydantic import BaseModel
    from runtime_api.endpoint import Handlers
    from runtime_api.loop import PREPARE, PhaseResult, Prepare

    from openswe.agent_api import server

    class Strict(BaseModel):
        count: int

    async def prepare(run: RemoteRun, request: Prepare) -> PhaseResult:
        # A bug in the phase's own code, not in what MDA sent.
        Strict.model_validate({"count": "many"})
        return PhaseResult()

    handlers = Handlers[RemoteRun]()
    handlers.serve(PREPARE, prepare)

    async def graph(run: RemoteRun, endpoint: str, body: JsonValue) -> JsonValue:
        return await handlers.dispatch(endpoint, run, body)

    monkeypatch.setattr(server, "_graphs", lambda: {"agent": graph})
    token = sign_runtime_token(RemoteRun(thread_id="t", assistant_id="agent", configurable={}))

    async def status(body: bytes) -> int:
        scope: Scope = {
            "type": "http",
            "method": "POST",
            "path": "/agent-api/prepare",
            "path_params": {"endpoint": "prepare"},
            "headers": [(b"authorization", f"Bearer {token}".encode())],
        }
        sent: list[Message] = []

        receive = _asgi_receive(body)

        async def send(message: Message) -> None:
            sent.append(message)

        await server.API(scope, receive, send)
        return sent[0]["status"]

    assert await status(b'{"sandbox_id": 3}') == 422
    assert "Agent API call failed" not in caplog.text
    assert await status(b'{"sandbox_id": "sb-1"}') == 500
    assert "Agent API call failed" in caplog.text


async def test_an_adopted_worker_acts_as_its_own_thread_never_its_coordinator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from unittest.mock import AsyncMock, MagicMock

    from runtime_api.catalog import Catalog
    from runtime_api.workers import Adopt

    from openswe.agent_api import async_workers, hosted
    from openswe.agent_api.graph import RunStart
    from openswe.prompts import prompt

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
    planned = Catalog(skill_sources=["/skills/worker/"])
    started = RunStart(catalog=planned, settings=RunSettings(draft_prs=True))
    graph = MagicMock(start_run=AsyncMock(return_value=started))
    monkeypatch.setattr(hosted, "hosted_graphs", lambda: {"agent": graph})

    # The worker's first message: the assignment its launch rendered.
    assignment = prompt(
        "tasks/assignment", coordinator_thread_id="coordinator", instructions="Fix it"
    )
    reply = await async_workers.adopt(
        grant, Adopt(thread_id="worker", run_id="run-1", instructions=assignment)
    )

    adopted = verify_runtime_token(reply.run_token)
    assert adopted.thread_id == "worker"
    assert adopted.configurable["thread_id"] == "worker"
    # Planned as the worker's own run.
    assert reply.catalog == planned
    assert graph.start_run.await_args.args[0].thread_id == "worker"
    # The settings the worker's run starts with ride in its token.
    assert adopted.settings == RunSettings(draft_prs=True)
    register.assert_awaited_once_with("actor", "call-1", "worker", "Fix it")


async def test_a_replayed_launch_reports_the_worker_it_registered_and_stops_the_other(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from unittest.mock import AsyncMock, MagicMock
    from uuid import uuid7

    from runtime_api.workers import Launched

    from openswe.agent_api import async_workers
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
        Launched(
            tool_call_id="call-1",
            instructions="Fix login",
            worker_thread_id="second-worker",
            worker_run_id="run-2",
        ),
    )

    assert reply.worker["worker_thread_id"] == "first-worker"
    client.runs.cancel.assert_awaited_once_with("second-worker", "run-2")


async def test_a_launched_run_that_ended_before_its_worker_was_registered_is_settled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from unittest.mock import AsyncMock, MagicMock

    from runtime_api.workers import Launched

    from openswe import completion
    from openswe.agent_api import async_workers
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
    body = Launched(
        tool_call_id="call-1",
        instructions="Fix login",
        worker_thread_id="worker",
        worker_run_id="run-1",
    )

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


@pytest.mark.parametrize(
    ("endpoint", "outcome"),
    [
        ("prepare", outcome)
        for outcome in (
            "disconnect",
            "disconnect_cancel",
            "cancel",
            "success",
            "unknown",
            "failure",
        )
    ]
    + [("operation", "finish"), ("run_ended", "finish")],
)
async def test_only_preparation_is_cancelled_on_disconnect(
    monkeypatch: pytest.MonkeyPatch, endpoint: str, outcome: str
) -> None:
    from runtime_api.endpoint import UnknownEndpointError

    started = asyncio.Event()
    finish = asyncio.Event()
    cleaning_up = asyncio.Event()
    allow_cleanup = asyncio.Event()
    cleaned_up = asyncio.Event()
    messages: asyncio.Queue[Message] = asyncio.Queue()
    messages.put_nowait({"type": "http.request", "body": b"{}", "more_body": False})
    sent: list[Message] = []

    async def graph(run: RemoteRun, name: str, body: JsonValue) -> JsonValue:
        try:
            started.set()
            if outcome == "finish":
                await finish.wait()
            if outcome in ("disconnect", "disconnect_cancel", "cancel"):
                await asyncio.Event().wait()
            if outcome == "unknown":
                raise UnknownEndpointError(name)
            if outcome == "failure":
                raise RuntimeError("checkout failed")
            return {"prepared": True}
        finally:
            cleaning_up.set()
            if outcome == "disconnect_cancel":
                await allow_cleanup.wait()
            await asyncio.sleep(0)
            cleaned_up.set()

    async def send(message: Message) -> None:
        sent.append(message)

    monkeypatch.setattr(server, "_graphs", lambda: {RUN.assistant_id: graph})
    scope: Scope = {
        "type": "http",
        "method": "POST",
        "path": f"/agent-api/{endpoint}",
        "headers": [],
        "path_params": {"endpoint": endpoint},
        "state": {"remote_run": RUN},
    }
    task = asyncio.create_task(server.api_endpoint(scope, messages.get, send))
    await asyncio.wait_for(started.wait(), 5)
    if outcome in ("disconnect", "disconnect_cancel", "finish"):
        messages.put_nowait({"type": "http.disconnect"})
    if outcome == "finish":
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        finish.set()
    if outcome == "disconnect_cancel":
        await asyncio.wait_for(cleaning_up.wait(), 5)
        task.cancel()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert not task.done()
        allow_cleanup.set()
    if outcome in ("cancel", "disconnect_cancel"):
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 5)
    else:
        await asyncio.wait_for(task, 5)
    assert cleaned_up.is_set()
    if outcome in ("disconnect", "disconnect_cancel", "cancel"):
        assert sent == []
    else:
        assert (
            sent[0]["status"]
            == {"success": 200, "finish": 200, "unknown": 404, "failure": 500}[outcome]
        )
        assert (
            json.loads(sent[1]["body"])
            == {
                "success": {"prepared": True},
                "finish": {"prepared": True},
                "unknown": {"detail": "Unknown endpoint prepare"},
                "failure": {"error": "RuntimeError", "message": "checkout failed"},
            }[outcome]
        )


@pytest.mark.parametrize(
    ("name", "arguments", "method"),
    [
        (
            "walkthrough_plan_chunk",
            {"title": "Change", "show": [], "explanation": "Why"},
            "plan_chunk",
        ),
        ("walkthrough_split_hunks", {"files": []}, "split"),
        ("walkthrough_move_to_other", {"files": []}, "move_to_other"),
        ("walkthrough_describe_other", {"summary": "Noise"}, "describe_other"),
    ],
)
@pytest.mark.parametrize("scout", [False, True])
@pytest.mark.parametrize("moved", [False, True])
async def test_walkthrough_errors_end_only_a_scout_with_a_stale_head(
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    arguments: dict[str, JsonValue],
    method: str,
    scout: bool,
    moved: bool,
) -> None:
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from runtime_api.loop import RunCall
    from runtime_api.operations import RunOperation

    from openswe.agent_api.graph import RunSession
    from openswe.review_scout.graph import PrepareReviewScoutRunMiddleware
    from openswe.sandboxes.tool_runtime import ModelChoice, SurfaceModels, ToolSurface
    from openswe.tools import plan_walkthrough, review_walkthrough
    from openswe.walkthrough.plan import RangeError
    from openswe.walkthrough.record import PlanMovedError

    error = (
        PlanMovedError("the pull request moved on; start from its new head")
        if moved
        else RangeError("another planner just placed some of these lines; check again")
    )
    workspace = AsyncMock()
    getattr(workspace, method).side_effect = error
    monkeypatch.setattr(plan_walkthrough, "_workspace", AsyncMock(return_value=workspace))
    monkeypatch.setattr(
        review_walkthrough.GuideContext,
        "current",
        AsyncMock(return_value=SimpleNamespace(workspace=workspace)),
    )
    config = {"configurable": {"thread_id": "scout" if scout else "guide"}}
    middleware = PrepareReviewScoutRunMiddleware(thread_id="scout", config=config)
    model = ModelChoice("anthropic:claude-sonnet-4-5", {})
    surface = ToolSurface(
        base_tools=[getattr(plan_walkthrough if scout else review_walkthrough, name)],
        middleware=[middleware] if scout else [],
        models=SurfaceModels(main=model, subagent=model, use_gateway=False),
    )
    await surface.prepare()
    session = RunSession(surface, config, None, started_here=True)
    state: dict[str, JsonValue] = {"scout_nothing_to_plan": False} if scout else {}

    result = await session.operation(
        RunOperation(name=name, arguments=arguments, state=state, request_id="call-1")
    )

    if scout and moved:
        assert result.state == {"scout_nothing_to_plan": True}
        state.update(result.state)
        next_step = await session.before_model(RunCall(state=state))
        assert next_step.jump_to == "end"
        assert next_step.call is None
        assert json.loads(str(result.content))["success"] is False
    else:
        assert result.state == {}
        assert json.loads(str(result.content)) == {"success": False, "error": str(error)}
        if scout:
            assert (await session.before_model(RunCall(state=state))).jump_to is None
