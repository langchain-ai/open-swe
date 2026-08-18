import asyncio
from typing import Any
from unittest.mock import AsyncMock

import pytest

from agent import server
from agent.dashboard import thread_api
from agent.prompt import construct_system_prompt


@pytest.mark.parametrize("enabled", [False, True])
def test_construct_system_prompt_gates_active_plan_mode(enabled: bool) -> None:
    prompt = construct_system_prompt(working_dir="/work", plan_mode=enabled)

    assert ("### Plan Mode (ACTIVE)" in prompt) is enabled


def test_dashboard_only_enters_plan_mode_on_explicit_request() -> None:
    dashboard_prompt = construct_system_prompt(working_dir="/work", source="dashboard")
    slack_prompt = construct_system_prompt(working_dir="/work", source="slack", slack_context=True)

    assert "call `enter_plan_mode` only when the user explicitly asks" in dashboard_prompt
    assert "If a task would genuinely benefit from a structured plan" in slack_prompt


def test_plan_mode_prompt_requests_slack_approval_options() -> None:
    prompt = construct_system_prompt(
        working_dir="/work", plan_mode=True, source="slack", slack_context=True
    )

    assert 'options=["Approve & implement", "Request changes"]' in prompt
    assert "do not send approval buttons" not in prompt


def test_plan_mode_excluded_tools_cover_mutating_tools() -> None:
    excluded = server.PLAN_MODE_EXCLUDED_TOOLS
    for tool in (
        "task",
        "manage_baby_sit",
        "open_pull_request",
        "recreate_sandbox",
        "request_pr_review",
        "save_user_skill",
        "delete_user_skill",
        "slack_move_thread",
        "slack_start_new_thread",
        "linear_create_issue",
        "linear_update_issue",
        "linear_delete_issue",
    ):
        assert tool in excluded
    # Read-only tools, plan-file editing tools, and explicit plan approval stay available.
    assert "approve_plan" not in excluded
    assert "read_file" not in excluded
    assert "write_file" not in excluded
    assert "edit_file" not in excluded
    assert "execute" not in excluded


class _FakeThreadsClient:
    async def create(
        self, *, thread_id: str, metadata: dict[str, Any], if_exists: str
    ) -> dict[str, Any]:
        return {"thread_id": thread_id, "metadata": metadata}

    async def update(self, *, thread_id: str, metadata: dict[str, Any]) -> dict[str, Any]:
        return {"thread_id": thread_id, "metadata": metadata}

    async def get(self, thread_id: str) -> dict[str, Any]:
        return {"thread_id": thread_id, "metadata": {}}


class _FakeRunsClient:
    def __init__(self) -> None:
        self.configurable: dict[str, Any] | None = None

    async def create(
        self,
        thread_id: str,
        assistant_id: str,
        *,
        input: dict[str, Any],
        config: dict[str, Any],
        if_not_exists: str = "reject",
        stream_mode: list[str] | None = None,
        stream_resumable: bool = False,
    ) -> dict[str, str]:
        self.configurable = config["configurable"]
        return {"run_id": "run-id"}


class _FakeLangGraphClient:
    def __init__(self) -> None:
        self.threads = _FakeThreadsClient()
        self.runs = _FakeRunsClient()


@pytest.fixture
def dashboard_run_client(monkeypatch: pytest.MonkeyPatch) -> _FakeLangGraphClient:
    client = _FakeLangGraphClient()

    async def fake_get_profile(login: str) -> dict[str, Any]:
        return {}

    async def fake_ensure_token(login: str) -> None:
        return None

    async def fake_resolve_email(login: str, profile: dict[str, Any]) -> str:
        return "octo@example.com"

    monkeypatch.setattr(thread_api, "langgraph_client", lambda: client)
    monkeypatch.setattr(thread_api, "get_profile", fake_get_profile)
    monkeypatch.setattr(thread_api, "_ensure_dashboard_github_token", fake_ensure_token)
    monkeypatch.setattr(thread_api, "_resolve_run_email", fake_resolve_email)
    return client


def _run_start_command(plan_mode: bool | None) -> dict[str, Any]:
    configurable: dict[str, Any] = {}
    if plan_mode is not None:
        configurable["plan_mode"] = plan_mode
    return {
        "method": "run.start",
        "params": {
            "input": {"messages": [{"role": "user", "content": "do work"}]},
            "config": {"configurable": configurable},
        },
    }


def test_run_start_passes_plan_mode_when_enabled(
    dashboard_run_client: _FakeLangGraphClient,
) -> None:
    enriched = asyncio.run(
        thread_api._enrich_run_start_command(
            "thread-id",
            "octo",
            _run_start_command(True),
            metadata={"source": "dashboard", "github_login": "octo"},
            creating=False,
        )
    )

    configurable = enriched["params"]["config"]["configurable"]
    assert configurable["plan_mode"] is True


def test_run_start_omits_plan_mode_when_disabled(
    dashboard_run_client: _FakeLangGraphClient,
) -> None:
    enriched = asyncio.run(
        thread_api._enrich_run_start_command(
            "thread-id",
            "octo",
            _run_start_command(None),
            metadata={"source": "dashboard", "github_login": "octo"},
            creating=False,
        )
    )

    configurable = enriched["params"]["config"]["configurable"]
    assert "plan_mode" not in configurable


async def test_thread_summary_reports_plan_mode() -> None:
    summary = await thread_api._thread_summary(
        {"thread_id": "t1", "metadata": {"source": "dashboard", "plan_mode": True}}
    )
    assert summary["planMode"] is True

    summary_off = await thread_api._thread_summary(
        {"thread_id": "t2", "metadata": {"source": "dashboard"}}
    )
    assert summary_off["planMode"] is False


async def test_enter_plan_mode_tool_returns_command() -> None:
    from langchain_core.messages import ToolMessage
    from langchain_core.tools import tool as as_tool
    from langgraph.types import Command

    from agent.tools.enter_plan_mode import enter_plan_mode

    # Wrap as the agent does so the InjectedToolCallId is supplied from the call.
    wrapped = as_tool(enter_plan_mode)
    result = await wrapped.ainvoke(
        {"name": "enter_plan_mode", "args": {}, "id": "call-1", "type": "tool_call"}
    )
    assert isinstance(result, Command)
    assert result.update is not None
    assert result.update["plan_mode"] is True
    messages = result.update["messages"]
    assert len(messages) == 1
    assert isinstance(messages[0], ToolMessage)
    assert messages[0].tool_call_id == "call-1"


async def test_mark_turn_checkpoint_records_the_plan_transition(monkeypatch) -> None:
    import importlib

    enter_plan_mode_module = importlib.import_module("agent.tools.enter_plan_mode")
    metadata = {
        "turn_checkpoints": [
            {
                "key": "msg-1",
                "ref": "refs/open-swe/turns/msg-1",
                "started_at": "t0",
                "repo_path": "/workspace/repo",
            }
        ]
    }

    class Threads:
        def __init__(self) -> None:
            self.get = AsyncMock(return_value={"metadata": metadata})
            self.update = AsyncMock()

    threads = Threads()
    client = type("Client", (), {"threads": threads})()
    backend = object()
    monkeypatch.setattr(enter_plan_mode_module, "get_client", lambda: client)
    monkeypatch.setattr(
        enter_plan_mode_module, "get_sandbox_backend", AsyncMock(return_value=backend)
    )
    record_plan = AsyncMock(return_value="refs/open-swe/turns/msg-1-plan")
    monkeypatch.setattr(enter_plan_mode_module, "record_plan_checkpoint", record_plan)

    await enter_plan_mode_module._mark_turn_checkpoint("thread-1", "msg-1")

    record_plan.assert_awaited_once_with(
        backend,
        None,
        "msg-1",
        repo_path="/workspace/repo",
    )
    threads.update.assert_awaited_once_with(
        thread_id="thread-1",
        metadata={
            "turn_checkpoints": [
                {
                    **metadata["turn_checkpoints"][0],
                    "plan_mode": True,
                    "plan_ref": "refs/open-swe/turns/msg-1-plan",
                }
            ]
        },
    )


async def test_enter_plan_mode_marks_the_current_turn_checkpoint(monkeypatch) -> None:
    import importlib

    from langchain_core.messages import HumanMessage

    enter_plan_mode_module = importlib.import_module("agent.tools.enter_plan_mode")
    set_plan_status = AsyncMock()
    mark_checkpoint = AsyncMock()
    monkeypatch.setattr(enter_plan_mode_module, "_thread_id_from_config", lambda: "thread-1")
    monkeypatch.setattr(enter_plan_mode_module, "set_plan_status", set_plan_status)
    monkeypatch.setattr(enter_plan_mode_module, "_mark_turn_checkpoint", mark_checkpoint)

    await enter_plan_mode_module.enter_plan_mode(
        tool_call_id="call-1",
        state={"messages": [HumanMessage(content="plan it", id="msg-1")]},
    )

    set_plan_status.assert_awaited_once_with("thread-1", "planning", plan_mode=True)
    mark_checkpoint.assert_awaited_once_with("thread-1", "msg-1")


def test_enter_plan_mode_exported() -> None:
    from agent.tools import enter_plan_mode

    assert callable(enter_plan_mode)


async def test_approve_plan_tool_exits_plan_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    import importlib

    from langchain_core.messages import ToolMessage
    from langgraph.types import Command

    from agent.tools import approve_plan as approve_plan_export

    approve_plan_tool = importlib.import_module("agent.tools.approve_plan")

    assert callable(approve_plan_export)

    saved: dict[str, Any] = {}

    monkeypatch.setattr(
        approve_plan_tool,
        "get_config",
        lambda: {
            "configurable": {
                "thread_id": "t1",
                "github_login": "octo",
                "user_email": "octo@example.com",
                "plan_mode": True,
            }
        },
    )

    async def fake_thread_metadata(thread_id: str) -> dict[str, Any]:
        assert thread_id == "t1"
        return {
            "source": "dashboard",
            "github_login": "octo",
            "triggering_user_email": "octo@example.com",
            "plan_mode": True,
            "plan_status": "ready",
        }

    async def fake_get_content(thread_id: str, *, raise_on_error: bool = False) -> dict[str, Any]:
        assert raise_on_error is True
        return {"markdown": "# Plan\n\nDo it", "status": "ready"}

    async def fake_list_comments(
        thread_id: str, *, raise_on_error: bool = False
    ) -> list[dict[str, Any]]:
        assert raise_on_error is True
        return [{"author": "Alice", "body": "add tests"}]

    async def fake_set_status(
        thread_id: str,
        status: str,
        *,
        plan_mode: Any = None,
        approved_by: Any = None,
    ) -> None:
        saved.update(
            thread_id=thread_id,
            status=status,
            plan_mode=plan_mode,
            approved_by=approved_by,
        )

    monkeypatch.setattr(approve_plan_tool, "_thread_metadata", fake_thread_metadata)
    monkeypatch.setattr(approve_plan_tool, "get_plan_content", fake_get_content)
    monkeypatch.setattr(approve_plan_tool, "list_plan_comments", fake_list_comments)
    monkeypatch.setattr(approve_plan_tool, "set_plan_status", fake_set_status)

    result = await approve_plan_tool.approve_plan(
        state={"plan_mode": True},
        tool_call_id="call-1",
    )

    assert isinstance(result, Command)
    assert result.update is not None
    assert result.update["plan_mode"] is False
    assert saved == {
        "thread_id": "t1",
        "status": "approved",
        "plan_mode": False,
        "approved_by": {"id": "octo", "name": "octo", "source": "agent"},
    }
    messages = result.update["messages"]
    assert len(messages) == 1
    assert isinstance(messages[0], ToolMessage)
    assert messages[0].tool_call_id == "call-1"
    assert "# Plan" in messages[0].content
    assert "add tests" in messages[0].content
    assert "reasonable engineering judgment" in messages[0].content
    assert "source of truth" not in messages[0].content


async def test_approve_plan_tool_ignores_stale_state_approver(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import importlib

    from langgraph.types import Command

    approve_plan_tool = importlib.import_module("agent.tools.approve_plan")
    saved: dict[str, Any] = {}

    monkeypatch.setattr(
        approve_plan_tool,
        "get_config",
        lambda: {
            "configurable": {
                "thread_id": "t1",
                "github_login": "current-user",
                "source": "dashboard",
                "plan_mode": True,
            }
        },
    )

    async def fake_thread_metadata(thread_id: str) -> dict[str, Any]:
        return {"github_login": "owner", "plan_mode": True}

    async def fake_get_content(thread_id: str, *, raise_on_error: bool = False) -> dict[str, Any]:
        return {"markdown": "# Plan", "status": "ready"}

    async def fake_list_comments(
        thread_id: str, *, raise_on_error: bool = False
    ) -> list[dict[str, Any]]:
        return []

    async def fake_set_status(
        thread_id: str,
        status: str,
        *,
        plan_mode: Any = None,
        approved_by: Any = None,
    ) -> None:
        saved.update(status=status, plan_mode=plan_mode, approved_by=approved_by)

    monkeypatch.setattr(approve_plan_tool, "_thread_metadata", fake_thread_metadata)
    monkeypatch.setattr(approve_plan_tool, "get_plan_content", fake_get_content)
    monkeypatch.setattr(approve_plan_tool, "list_plan_comments", fake_list_comments)
    monkeypatch.setattr(approve_plan_tool, "set_plan_status", fake_set_status)

    result = await approve_plan_tool.approve_plan(
        state={
            "plan_mode": True,
            "plan_approver": {
                "id": "teammate",
                "name": "Teammate",
                "source": "dashboard",
            },
        },
        tool_call_id="call-1",
    )

    assert isinstance(result, Command)
    assert saved == {
        "status": "approved",
        "plan_mode": False,
        "approved_by": {
            "id": "current-user",
            "name": "current-user",
            "source": "dashboard",
        },
    }


async def test_approve_plan_tool_allows_non_owner_configurable_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import importlib

    from langgraph.types import Command

    approve_plan_tool = importlib.import_module("agent.tools.approve_plan")
    saved: dict[str, Any] = {}
    monkeypatch.setattr(
        approve_plan_tool,
        "get_config",
        lambda: {
            "configurable": {
                "thread_id": "t1",
                "github_login": "other",
                "source": "linear",
                "plan_mode": True,
            }
        },
    )

    async def fake_thread_metadata(thread_id: str) -> dict[str, Any]:
        return {"github_login": "owner", "plan_mode": True}

    async def fake_get_content(thread_id: str, *, raise_on_error: bool = False) -> dict[str, Any]:
        return {"markdown": "# Plan", "status": "ready"}

    async def fake_list_comments(
        thread_id: str, *, raise_on_error: bool = False
    ) -> list[dict[str, Any]]:
        return []

    async def fake_set_status(
        thread_id: str,
        status: str,
        *,
        plan_mode: Any = None,
        approved_by: Any = None,
    ) -> None:
        saved["approved_by"] = approved_by

    monkeypatch.setattr(approve_plan_tool, "_thread_metadata", fake_thread_metadata)
    monkeypatch.setattr(approve_plan_tool, "get_plan_content", fake_get_content)
    monkeypatch.setattr(approve_plan_tool, "list_plan_comments", fake_list_comments)
    monkeypatch.setattr(approve_plan_tool, "set_plan_status", fake_set_status)

    result = await approve_plan_tool.approve_plan(state={"plan_mode": True}, tool_call_id="call-1")

    assert isinstance(result, Command)
    assert saved["approved_by"] == {"id": "other", "name": "other", "source": "linear"}


@pytest.mark.parametrize(
    "reply",
    [
        "approve",
        "Approve & implement",
        "Approved!",
        "Looks good to me.",
        "go ahead",
        "ship it",
        "yes",
    ],
)
def test_natural_language_plan_approval_accepts_affirmative_replies(reply: str) -> None:
    from agent.webhooks.slack import _is_natural_language_plan_approval

    assert _is_natural_language_plan_approval(reply) is True


@pytest.mark.parametrize(
    "reply",
    [
        "do not approve",
        "No, revise the plan",
        "approve after these changes",
        "looks mostly good, but change the tests",
        "cancel",
        "what changed?",
    ],
)
def test_natural_language_plan_approval_rejects_ambiguous_or_negative_replies(reply: str) -> None:
    from agent.webhooks.slack import _is_natural_language_plan_approval

    assert _is_natural_language_plan_approval(reply) is False
