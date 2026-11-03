import asyncio
import json
import logging
import time
from collections.abc import Mapping, Sequence

import jwt
import pytest
from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from pydantic import JsonValue
from runtime_api.operations import OPERATION, OperationResult, RunOperation
from starlette.types import Message, Receive, Scope, Send

from openswe.agent_api import server
from openswe.agent_api.agent import AgentKind
from openswe.agent_api.run_calls import TokenRun
from openswe.agent_api.run_kinds import RunKind
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
from openswe.sandboxes.tool_runtime import ToolSurface

RUN = RemoteRun(
    thread_id="thread-1",
    assistant_id="reviewer",
    configurable={"repo": {"owner": "acme", "name": "widgets"}, "pr_number": 7},
    expires_at=int(time.time()) + 3600,
)


class _NeverPlanned(AgentKind):
    """The coding agent, whose calls after its start never work its plan out again."""

    async def catalog(self, config: RunnableConfig, surface: ToolSurface) -> None:
        raise AssertionError("Only starting a run plans it")


class _Offering(RunKind):
    """A graph whose runs' operations reach ``tools``, put to ``shell_guards`` first."""

    assistant_id = "agent"

    def __init__(self, *tools: BaseTool, shell_guards: Sequence[AgentMiddleware] = ()) -> None:
        self._tools = {tool.name: tool for tool in tools}
        self.shell_guards = tuple(shell_guards)

    async def tool(self, config: RunnableConfig, name: str) -> BaseTool | None:  # noqa: ARG002
        return self._tools.get(name)

    async def operate(self, request: RunOperation, run: RemoteRun = RUN) -> OperationResult:
        """``request`` as ``run`` calls it, with its catalog's grant."""
        granted = request.model_copy(update={"grant": run.grant(request.name)})
        return OperationResult.model_validate(
            await TokenRun.answer(self, run, OPERATION.name, granted.model_dump(mode="json"))
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


async def test_the_named_calls_around_a_model_call_never_plan_the_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from unittest.mock import AsyncMock

    from langchain_core.messages import HumanMessage, messages_to_dict

    from openswe.agent_api import run_sandbox
    from openswe.middleware.model_selection import RouteSelection
    from openswe.middleware.task_coordination import TaskCoordinationMiddleware
    from openswe.utils.model import provider_model_kwargs

    classify = AsyncMock(return_value=RouteSelection("fast"))
    refresh = AsyncMock(return_value=None)
    monkeypatch.setattr("openswe.middleware.model_selection._select_jev_route", classify)
    monkeypatch.setattr(run_sandbox, "refresh_thread_github_proxy", refresh)
    monkeypatch.setattr(TaskCoordinationMiddleware, "for_thread", AsyncMock(return_value=None))
    parts = _NeverPlanned()
    monkeypatch.setattr(
        parts, "credentials", AsyncMock(side_effect=AssertionError("No GitHub credentials needed"))
    )
    run = RUN.model_copy(update={"assistant_id": "agent"})
    asked = messages_to_dict([HumanMessage(content="Fix the login", id="human-1")])

    route = await TokenRun.answer(parts, run, "route_model", {"run_id": "run-1", "humans": asked})
    chosen = await TokenRun.answer(
        parts,
        run,
        "model_choice",
        {"run_id": "run-1", "model_id": "anthropic:claude-opus-5-5", "effort": "high"},
    )
    context = await TokenRun.answer(parts, run, "task_context", {"run_id": "run-1"})
    refreshed = await TokenRun.answer(parts, run, "refresh_github_proxy", {"run_id": "run-1"})

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
    # The coding agent narrows its token no further than its thread does.
    refresh.assert_awaited_once_with(
        run.thread_id,
        workspace_slug=None,
        github_proxy_repositories=None,
        github_proxy_permissions=None,
    )


async def test_an_agent_turn_is_prepared_from_its_token_and_its_start_recorded_off_its_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Preparing a turn plans nothing, reaches no sandbox MDA keeps and records nothing:
    what the turn's start records waits for its ``run_started`` report."""
    from unittest.mock import AsyncMock, MagicMock

    from langchain_core.messages import HumanMessage, messages_to_dict
    from runtime_api.catalog import RoutingRules

    from openswe import server
    from openswe.agent_api import agent, run_calls, run_sandbox
    from openswe.agent_api.tokens import AgentTurnSettings, RunSettings
    from openswe.prompt import SystemPrompt

    def never_reached(thread_id: str) -> object:
        raise AssertionError("Preparing a turn never reaches the sandbox MDA keeps")

    bound = AsyncMock()
    updated = AsyncMock()
    usage = AsyncMock()
    prompts: list[dict[str, object]] = []
    monkeypatch.setattr(run_sandbox, "bind_mda_sandbox", bound)
    # Binding the sandbox found its workspace image stale.
    monkeypatch.setattr(run_calls, "take_stale_boot", lambda thread_id: object())
    monkeypatch.setattr(
        run_calls, "stale_workspace_notice", lambda thread_id, stale: {"workspace": "w"}
    )
    monkeypatch.setattr(agent, "load_thread_settings", AsyncMock(return_value={}))
    monkeypatch.setattr(server, "get_or_create_sandbox_backend_proxy", never_reached)
    monkeypatch.setattr(server, "get_sandbox_metadata", AsyncMock(return_value={}))
    monkeypatch.setattr(server, "client", MagicMock(threads=MagicMock(update=updated)))
    monkeypatch.setattr(server, "record_agent_invocation_usage", usage)
    monkeypatch.setattr(server, "resolve_github_token", AsyncMock(return_value=("token", None)))
    monkeypatch.setattr(server, "schedule_thread_title_generation", lambda **kwargs: None)

    def render(**kwargs: object) -> SystemPrompt:
        prompts.append(kwargs)
        return SystemPrompt(shared="You code.", thread="")

    monkeypatch.setattr(server, "construct_system_prompt", render)
    for name in (
        "resolve_triggering_user_identity",
        "load_workspace",
        "_resolve_prompt_default_repo",
    ):
        monkeypatch.setattr(server, name, AsyncMock(return_value=None))
    parts = _NeverPlanned()
    turn = AgentTurnSettings(
        title_model_id="openai:gpt-6-luna",
        routing=RoutingRules(route_model_ids={"default": "openai:gpt-6.1-sol"}),
    )
    run = RUN.model_copy(
        update={
            "assistant_id": "agent",
            "configurable": {"source": "dashboard", "invocation_id": "invocation-1"},
            "settings": RunSettings(model_id="openai:gpt-6.1-sol", effort="high", agent=turn),
            "grant_id": "grant-1",
        }
    )
    asked = messages_to_dict([HumanMessage(content="Fix the login", id="human-1")])

    prepared = await TokenRun.answer(
        parts,
        run,
        "prepare",
        {"run_id": "run-1", "messages": asked, "in_sandbox": True, "sandbox_id": "sandbox-1"},
    )

    assert isinstance(prepared, dict) and isinstance(update := prepared["update"], dict)
    assert update["work_dir"] == "/workspace"
    assert update["selected_model_id"] == "openai:gpt-6.1-sol"
    assert len(prompts) == 1
    bound.assert_awaited_once_with("thread-1", "sandbox-1", open_error=None, workspace_slug=None)
    updated.assert_not_awaited()
    usage.assert_not_awaited()
    # The transcript turn may open on another replica, so its notices go with the run.
    assert prepared["notices"] == [{"kind": "workspace_stale", "data": {"workspace": "w"}}]
    queued = MagicMock()
    monkeypatch.setattr(run_calls, "queue_run_notice", queued)

    await TokenRun.answer(
        parts,
        run,
        "run_started",
        {
            "run_id": "run-1",
            "humans": asked,
            "model_id": "openai:gpt-6.1-sol",
            "effort": "high",
            "model_route": "default",
            "notices": prepared["notices"],
            "sender": prepared["sender"],
        },
    )

    queued.assert_called_once_with("thread-1", "workspace_stale", {"workspace": "w"})
    [metadata] = [call.kwargs["metadata"] for call in updated.await_args_list]
    assert metadata["model"] == "openai:gpt-6.1-sol"
    assert metadata["model_route"] == "default"
    assert usage.await_args.kwargs["model_id"] == "openai:gpt-6.1-sol"
    assert usage.await_args.kwargs["invocation_id"] == "invocation-1"

    # An earlier run's start, sent by this one from the thread's checkpoint, is recorded
    # as its own sender's, and leaves this run's model on the thread.
    await TokenRun.answer(
        parts,
        run,
        "run_started",
        {
            "run_id": "run-0",
            "humans": asked,
            "invocation_id": "invocation-0",
            "model_id": "openai:gpt-6-luna",
            "sender": {"github_login": "earlier-sender", "source": "slack"},
        },
    )

    assert usage.await_args.kwargs["invocation_id"] == "invocation-0"
    assert usage.await_args.kwargs["github_login"] == "earlier-sender"
    assert usage.await_args.kwargs["source"] == "slack"
    assert updated.await_count == 1


async def test_a_closed_walkthroughs_next_turn_still_retires_its_rules(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The guide stays after a walkthrough closes, to tell the model its rules ended;
    only an open one pins the checkout in the sandbox."""
    from unittest.mock import AsyncMock

    from openswe.agent_api import agent
    from openswe.agent_api.tokens import AgentTurnSettings, RunSettings
    from openswe.review_guide.middleware import ReviewGuideMiddleware

    monkeypatch.setattr(agent, "load_thread_settings", AsyncMock(return_value={}))
    turn = AgentTurnSettings(title_model_id="openai:gpt-6-luna", review_guide_closed=True)

    prepared = await agent.AgentKind().preparation(
        {"configurable": {"thread_id": "thread-1"}}, RunSettings(agent=turn), sandbox_id="sandbox-1"
    )

    assert prepared is not None
    assert any(isinstance(middleware, ReviewGuideMiddleware) for middleware in prepared)
    assert not turn.reaches_sandbox


async def test_task_context_rechecks_membership_and_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from unittest.mock import AsyncMock
    from uuid import uuid4

    from openswe.middleware import task_coordination
    from openswe.tasks import store

    task = store.Task(title="Repair login", workspace_id=uuid4(), coordinator_thread_id="owner")
    context = store.TaskContext(
        task, store.TaskMembership(thread_id=RUN.thread_id, task_id=task.id, role="worker")
    )
    membership = AsyncMock(return_value=None)
    delegation = store.TaskDelegation(
        worker_thread_id=RUN.thread_id,
        task_id=task.id,
        coordinator_thread_id="owner",
        instructions="Fix login",
        model="openai:gpt-5.5",
        effort="high",
    )
    thread = AsyncMock(return_value={"metadata": {}})
    monkeypatch.setattr(task_coordination, "read_thread", thread)
    monkeypatch.setattr(task_coordination, "maybe_refresh_proxy_token", AsyncMock())
    monkeypatch.setattr(store.TaskMembership, "context_for_thread", membership)
    monkeypatch.setattr(store.TaskDelegation, "get", AsyncMock(return_value=delegation))
    parts = AgentKind()
    run = RUN.model_copy(update={"assistant_id": "agent"})

    absent = await TokenRun.answer(parts, run, "task_context", {"run_id": "run-1"})
    assert isinstance(absent, dict) and absent["message"] is None

    membership.return_value = context
    joined = await TokenRun.answer(parts, run, "task_context", {"run_id": "run-1"})
    assert isinstance(joined, dict)
    assert str(task.id) in str(joined["message"])
    assert task.title in str(joined["message"])
    assert joined["instructions"] != absent["instructions"]

    delegation.cancelled = True
    with pytest.raises(PermissionError, match="This worker was cancelled"):
        await TokenRun.answer(parts, run, "task_context", {"run_id": "run-1"})

    thread.side_effect = PermissionError("Thread access revoked")
    with pytest.raises(PermissionError, match="Thread access revoked"):
        await TokenRun.answer(parts, run, "task_context", {"run_id": "run-1"})


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

    from openswe.agent_api import run_calls

    told = AsyncMock(side_effect=[RuntimeError("Slack is down"), None])
    settled = AsyncMock()
    monkeypatch.setattr(run_calls, "notify_step_limit", told)
    monkeypatch.setattr(run_calls, "settle_review_check", settled)
    parts = _NeverPlanned()
    run = RUN.model_copy(update={"assistant_id": "agent"})
    batch = Reports(
        items=[
            item(endpoint, RunRequest(run_id="run-1"), key=f"run-1:{endpoint.name}", trace={})
            for endpoint in (STEP_LIMIT_REACHED, SETTLE_REVIEW_CHECK)
        ]
    ).model_dump(mode="json")

    async def deliver() -> ReportsTaken:
        return ReportsTaken.model_validate(await TokenRun.answer(parts, run, "reports", batch))

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

    from openswe.agent_api import run_calls
    from openswe.agent_api.report_receipts import ReportReceipts

    settled = AsyncMock()
    monkeypatch.setattr(run_calls, "settle_review_check", settled)
    kept = ReportReceipts.record
    monkeypatch.setattr(
        ReportReceipts, "record", AsyncMock(side_effect=[RuntimeError("database went away")])
    )
    parts = _NeverPlanned()
    run = RUN.model_copy(update={"assistant_id": "agent"})
    batch = Reports(
        items=[item(SETTLE_REVIEW_CHECK, RunRequest(run_id="run-1"), key="run-1:settle", trace={})]
    ).model_dump(mode="json")

    async def deliver() -> ReportsTaken:
        return ReportsTaken.model_validate(await TokenRun.answer(parts, run, "reports", batch))

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

    @tool
    def draft_preference(note: str) -> str:
        """Say whether this run opens pull requests as drafts."""
        return f"{note}: {get_config()['configurable'].get('draft_prs')}"

    monkeypatch.setattr(
        agent, "agent_tools_by_name", lambda: {draft_preference.name: draft_preference}
    )
    monkeypatch.setattr(agent, "resolve_github_token", AsyncMock())
    parts = _NeverPlanned()
    # The settings decided as the run started, signed into its token.
    run = RUN.model_copy(update={"assistant_id": "agent", "settings": RunSettings(draft_prs=False)})
    call = {
        "run_id": "run-1",
        "name": "draft_preference",
        "arguments": {"note": "drafts"},
        "request_id": "call-1",
    }

    result = await TokenRun.answer(
        parts, run, "operation", {**call, "grant": run.grant("draft_preference")}
    )
    # Only an operation the run's catalog granted it runs, however the call names it.
    ungranted = await TokenRun.answer(parts, run, "operation", call)
    another_run = run.model_copy(update={"grant_id": "another-run"})
    regranted = await TokenRun.answer(
        parts, run, "operation", {**call, "grant": another_run.grant("draft_preference")}
    )
    verdict = await TokenRun.answer(
        parts, run, "check_shell", {"run_id": "run-1", "tool": "execute", "command": "ls"}
    )

    assert isinstance(result, dict) and result["content"] == "drafts: False"
    assert verdict == {"allowed": True, "command": None, "refusal": None}
    for refused in (ungranted, regranted):
        assert isinstance(refused, dict) and refused["status"] == "error"
        assert "not one of this run's operations" in str(refused["content"])


@pytest.mark.parametrize("environment", ["preview", "staging", "production"])
async def test_remote_prompts_include_deployment_context(
    monkeypatch: pytest.MonkeyPatch, environment: str
) -> None:
    from openswe.agent_api.run_catalog import RunCatalog
    from openswe.sandboxes.tool_runtime import ModelChoice, SurfaceModels, SurfaceSubagent

    monkeypatch.setenv("OPENSWE_ENV", environment)
    model = ModelChoice("openai:gpt-6.1-sol", {})
    surface = ToolSurface(
        models=SurfaceModels(main=model, subagent=model, use_gateway=False),
        subagents=[SurfaceSubagent("worker", "worker", "subagent instructions")],
    )
    catalog = RunCatalog(RunKind(), RUN, surface, {}).build()
    # MDA puts the deployment's context ahead of the main prompt it renders.
    main_context = catalog.loop.deployment_prompt
    subagent_prompt = catalog.subagents[0].system_prompt

    if environment == "production":
        assert main_context == ""
        assert subagent_prompt == "subagent instructions"
    else:
        for rendered in (main_context, subagent_prompt):
            assert rendered.count(f"**{environment}**") == 1
        assert subagent_prompt.endswith("subagent instructions")


async def test_app_lifespan_exits_without_managed_deep_agents(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openswe.agent_api.client import RemoteRuntimeConfigurationError
    from openswe.api.app import app, lifespan

    monkeypatch.delenv("MDA_RUNTIME_URL", raising=False)

    with pytest.raises(RemoteRuntimeConfigurationError, match="MDA_RUNTIME_URL is required"):
        async with lifespan(app):
            pass


async def test_reviewer_tools_get_a_github_token_on_a_replica_that_did_not_prepare_the_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import openswe.reviewer as reviewer
    from openswe.agent_api.reviewer import ReviewerKind
    from openswe.github.thread_token import (
        invalidate_cached_github_token,
        resolve_thread_github_token,
    )

    async def mint(*, repositories: list[str] | None = None) -> tuple[str, str | None]:
        assert repositories == ["widgets"]
        return "ghs_reviewer", None

    monkeypatch.setattr(reviewer, "get_github_app_installation_token_with_expiry", mint)
    await invalidate_cached_github_token(RUN.thread_id)
    run = RUN.model_copy(update={"configurable": {**RUN.configurable, "source": "github"}})
    call = TokenRun(ReviewerKind(), run, "run-1")

    # Whatever call of the run lands here first readies its token before its work.
    token = await call.in_run("operation", lambda: resolve_thread_github_token(call.config))

    assert token == "ghs_reviewer"


@pytest.mark.usefixtures("registry_db")
async def test_reviewer_runs_record_transcript_model_and_usage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from unittest.mock import AsyncMock, MagicMock
    from uuid import uuid7

    from langchain_core.messages import AIMessage, HumanMessage, messages_to_dict

    import openswe.reviewer as reviewer
    from openswe.agent_api.reviewer import ReviewerKind
    from openswe.database import analytics
    from openswe.sandboxes.managed import ManagedSandbox
    from openswe.transcript.snapshot import load_snapshot

    monkeypatch.setattr(analytics, "_WORKSPACE_ID", None)
    await analytics.load_workspace()
    thread_id, run_id = str(uuid7()), str(uuid7())
    configurable = {"thread_id": thread_id, "invocation_id": run_id, "source": "github"}
    run = RUN.model_copy(update={"thread_id": thread_id, "configurable": configurable})
    metadata: dict[str, object] = {}
    client = MagicMock()
    client.threads.get = AsyncMock(return_value={"metadata": metadata})
    client.threads.update = AsyncMock(side_effect=lambda *_, **kw: metadata.update(kw["metadata"]))
    monkeypatch.setattr(
        reviewer.THREADS,
        "update",
        AsyncMock(side_effect=lambda _id, update: metadata.update(update)),
    )
    monkeypatch.setattr("openswe.middleware.transcript.get_client", lambda: client)
    monkeypatch.setattr(reviewer, "prepare_reviewer_run", AsyncMock(return_value={}))
    monkeypatch.setattr(ManagedSandbox, "for_thread", AsyncMock(return_value=None))
    schedule = AsyncMock(return_value=True)
    monkeypatch.setattr("openswe.agent_cost.schedule_agent_cost_refresh", schedule)
    asked = HumanMessage(content="Review this PR", id="human")
    reply = AIMessage(
        content="Reviewed",
        id="reply",
        usage_metadata={"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
    )

    parts = ReviewerKind()
    await TokenRun.answer(
        parts, run, "prepare", {"run_id": run_id, "messages": messages_to_dict([asked])}
    )
    await TokenRun.answer(
        parts, run, "model_result", {"run_id": run_id, "response": messages_to_dict([reply])}
    )
    await TokenRun.answer(parts, run, "run_ended", {"run_id": run_id})

    assert metadata["agent_kind"] == "reviewer" and metadata["model"]
    snapshot = await load_snapshot(thread_id)
    assert snapshot is not None and snapshot.turns[0].state == "completed"
    assert snapshot.messages[-1].usage["invocation_id"] == run_id
    assert schedule.await_args.args[0]["invocation_id"] == run_id


@pytest.mark.parametrize("additional", [True, {"type": "string"}, False, None])
async def test_operation_respects_open_tool_argument_schemas(additional: JsonValue) -> None:
    from langchain_core.tools import StructuredTool

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
    arguments: dict[str, JsonValue] = {"repo": "open-swe", "commit": "abc123"}

    result = await _Offering(tool).operate(
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


@pytest.mark.parametrize("worker", [False, True])
async def test_langsmith_runtime_tokens_and_worker_grants_recheck_live_access(
    monkeypatch: pytest.MonkeyPatch, worker: bool
) -> None:
    from unittest.mock import AsyncMock

    from openswe.dashboard.langsmith_login import LangSmithLogin

    grant = AsyncMock(return_value="langsmith-token")
    monkeypatch.setattr(LangSmithLogin, "access_token", grant)
    run = RUN.model_copy(
        update={
            "configurable": {
                "auth_provider": "langsmith",
                "user_id": "019abcde-0000-7000-8000-000000000001",
            }
        }
    )
    token = (
        sign_worker_grant(WorkerGrant(coordinator=run, request_id="worker"))
        if worker
        else sign_runtime_token(run)
    )
    assert (await _status(f"Bearer {token}"))[0] == 200
    grant.return_value = None
    assert await _status(f"Bearer {token}") == (401, None)


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
        "path": "/agent-api/route_model",
        "headers": [],
        "path_params": {"endpoint": "route_model"},
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
    assert all(value in lines[0] for value in ("mda-run-1", "thread-1", "route_model"))


async def test_mda_catalog_leaves_integrations_and_code_mode_to_mda(
    monkeypatch: pytest.MonkeyPatch, registry_db: None
) -> None:
    """Seldom-needed tools go to MDA's code mode, not the model's schemas; the graph's own stays here.

    Integrations stay out of the catalog: the backend keeps them and finds them for
    the run's ``search_tools`` and code mode.
    """
    from unittest.mock import AsyncMock

    from openswe.sandboxes.managed import ManagedSandbox

    # A thread with no sandbox of MDA's, so the catalog reads no thread metadata.
    monkeypatch.setattr(ManagedSandbox, "for_thread", AsyncMock(return_value=None))
    monkeypatch.setenv("LANGCHAIN_REVISION_ID", "current-backend-sha")
    from langchain_core.tools import StructuredTool

    from openswe.agent_api.run_catalog import RunCatalog
    from openswe.mcp.models import MCPToolProvenance
    from openswe.middleware.dynamic_tools import DynamicToolMiddleware
    from openswe.sandboxes.tool_runtime import ModelChoice, SurfaceModels, SurfaceSubagent

    def integration(name: str) -> StructuredTool:
        async def run(query: str) -> str:
            return query

        return StructuredTool.from_function(coroutine=run, name=name, description=name)

    class Coordinator(RunKind):
        async def catalog(self, config: RunnableConfig, surface: ToolSurface) -> None:
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
            surface.base_tools = [
                integration(name)
                for name in (
                    "web_search",
                    "manage_thread",
                    "slack_reply",
                    "message_task_thread",
                    "load_integration_tools",
                )
            ]
            surface.code_mode = True
            surface.excluded = frozenset({"grep", "glob"})
            surface.delegates = True
            surface.model_call_limit = 20
            surface.subagents = [SurfaceSubagent("general-purpose", "helper", "instructions")]
            choice = ModelChoice("openai:gpt-6.1-sol", {})
            surface.models = SurfaceModels(main=choice, subagent=choice, use_gateway=False)

    parts = Coordinator()
    started = await (await RunCatalog.of(parts, RUN)).start()
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
        isinstance(op, dict) and RUN.granted(str(op["name"]), str(op["grant"])) for op in operations
    )

    # The run's context names its operations; it reads their specs and grants by its token.
    from runtime_api.tool_search import ResolvedTool, ToolMatches, ToolSpecs

    started_run = RUN.model_copy(update={"settings": started.settings})
    names = [str(op["name"]) for op in operations if isinstance(op, dict)]
    listed = ToolSpecs.model_validate(
        await TokenRun.answer(parts, started_run, "tool_specs", {"names": names})
    )
    assert sorted(spec.name for spec in listed.tools) == sorted(names)
    assert all(RUN.granted(spec.name, spec.grant) for spec in listed.tools)
    # The run's search and code mode reach what its catalog leaves out, by its token.
    found = ToolMatches.model_validate(
        await TokenRun.answer(
            parts, started_run, "search_tools", {"query": "mcp search", "limit": 1}
        )
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
        await TokenRun.answer(parts, started_run, "resolve_tool", {"name": "mcpSearch"})
    )
    assert looked_up.tool is not None and looked_up.tool.name == "mcp_search"
    assert RUN.granted("mcp_search", looked_up.tool.grant)
    # Called directly, by its own name, it is the same operation.
    by_name = ResolvedTool.model_validate(
        await TokenRun.answer(parts, started_run, "resolve_tool", {"name": "mcp_search"})
    )
    assert by_name.tool == looked_up.tool
    unknown = await TokenRun.answer(
        parts, started_run, "resolve_tool", {"name": "load_integration_tools"}
    )
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

    from openswe.agent_api.operations import Injections

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

    reads = Injections.of(switch)
    result = await _Offering(switch).operate(
        RunOperation(name="switch", state={"model_route": "fast"}, request_id="call-1")
    )

    assert reads.reads == ["model_route"]
    assert not reads.reads_all
    assert result.content == "switched"
    assert result.state == {"requested_model": "after-fast"}


async def test_the_files_api_reaches_the_threads_files_as_the_run() -> None:
    """A bridge checks who the run is for, which it reads from the run's own config."""
    from deepagents.backends.protocol import (
        BackendProtocol,
        ExecuteResponse,
        SandboxBackendProtocol,
    )

    from openswe.run_config import RunConfig

    seen: list[str | None] = []

    class Machine(SandboxBackendProtocol):
        @property
        def id(self) -> str:
            return "machine"

        async def aexecute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
            seen.append(RunConfig.from_runtime().thread_id)
            return ExecuteResponse(output=f"ran {command}", exit_code=0)

    class OnTheMachine(RunKind):
        async def files(self, config: RunnableConfig) -> BackendProtocol:  # noqa: ARG002
            return Machine()

    reply = await TokenRun.answer(OnTheMachine(), RUN, "files.execute", {"command": "ls"})

    assert reply == {"output": "ran ls", "exit_code": 0, "truncated": False}
    assert seen == ["thread-1"]


async def test_a_cancelled_operation_ends_the_call_rather_than_answering() -> None:
    from langchain_core.tools import tool
    from langgraph.errors import NodeCancelledError

    @tool
    async def wait() -> str:
        """Wait for something."""
        raise NodeCancelledError("tools")

    with pytest.raises(NodeCancelledError):
        await _Offering(wait).operate(RunOperation(name="wait", request_id="call-1"))


async def test_an_operation_that_runs_a_command_is_put_to_the_shell_guards_first() -> None:
    from langchain_core.tools import tool

    from openswe.middleware.pr_creation_guard import PullRequestCreationGuardMiddleware

    ran: list[str] = []

    @tool
    def background_execute(command: str) -> str:
        """Run a command in the background."""
        ran.append(command)
        return "started"

    parts = _Offering(background_execute, shell_guards=[PullRequestCreationGuardMiddleware()])

    refused = await parts.operate(
        RunOperation(
            name="background_execute",
            arguments={"command": "gh pr create --fill"},
            request_id="call-1",
        )
    )
    started = await parts.operate(
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
        if called not in {"prepare", "route_model", "run_ended"}:
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
        "path": "/agent-api/route_model",
        "path_params": {"endpoint": "route_model"},
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
    assert hook["name"] == "route_model"
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
        "route_model",
        "operation",
        "run_ended",
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
    from runtime_api.loop import PREPARE, Prepare, Prepared

    from openswe.agent_api import server

    class Strict(BaseModel):
        count: int

    async def prepare(run: RemoteRun, request: Prepare) -> Prepared:
        # A bug in the phase's own code, not in what MDA sent.
        Strict.model_validate({"count": "many"})
        return Prepared()

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

    from openswe.agent_api import async_workers
    from openswe.agent_api.run_catalog import RunCatalog, RunStart
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
    starting = AsyncMock(return_value=MagicMock(start=AsyncMock(return_value=started)))
    monkeypatch.setattr(RunCatalog, "of", starting)

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
    assert starting.await_args.args[1].thread_id == "worker"
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

    from langchain_core.tools import tool

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
    run = RUN.model_copy(update={"thread_id": "scout" if scout else "guide"})
    parts = _Offering(tool(getattr(plan_walkthrough if scout else review_walkthrough, name)))
    state: dict[str, JsonValue] = {"scout_nothing_to_plan": False} if scout else {}

    result = await parts.operate(
        RunOperation(name=name, arguments=arguments, state=state, request_id="call-1"), run
    )

    # The flag is what ends the scout's run, in MDA's loop.
    if scout and moved:
        assert result.state == {"scout_nothing_to_plan": True}
        assert json.loads(str(result.content))["success"] is False
    else:
        assert result.state == {}
        assert json.loads(str(result.content)) == {"success": False, "error": str(error)}


async def test_langsmith_cloud_run_delegates_to_credential_isolated_sandbox(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from unittest.mock import AsyncMock

    from openswe.agent_api import run_sandbox

    create = AsyncMock(return_value="managed-cloud")
    monkeypatch.setattr(run_sandbox.ManagedSandbox, "for_thread", create)
    result = await run_sandbox.RunSandbox(
        {
            "configurable": {
                "thread_id": "cloud-thread",
                "auth_provider": "langsmith",
                "user_id": "019abcde-0000-7000-8000-000000000001",
            }
        },
        reviews=False,
    ).managed()
    assert result == "managed-cloud"
    assert create.call_args.args[0] == "cloud-thread"
    assert create.call_args.kwargs["allow_github"] is False
