from __future__ import annotations

import asyncio
from typing import Any

import pytest

from agent import server
from agent.dashboard import thread_api
from agent.prompt import construct_system_prompt


def test_plan_mode_prompt_included_when_enabled() -> None:
    prompt = construct_system_prompt(working_dir="/work", plan_mode=True)
    assert "Plan Mode (ACTIVE)" in prompt
    assert "read-only research-and-planning phase" in prompt


def test_plan_mode_prompt_absent_by_default() -> None:
    prompt = construct_system_prompt(working_dir="/work")
    assert "Plan Mode (ACTIVE)" not in prompt


def test_plan_mode_excluded_tools_cover_mutating_tools() -> None:
    excluded = server.PLAN_MODE_EXCLUDED_TOOLS
    for tool in (
        "task",
        "open_pull_request",
        "request_pr_review",
        "slack_start_new_thread",
        "linear_create_issue",
        "linear_update_issue",
        "linear_delete_issue",
    ):
        assert tool in excluded
    # Read-only tools and plan-file editing tools must stay available.
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


def test_thread_summary_reports_plan_mode() -> None:
    summary = thread_api._thread_summary(
        {"thread_id": "t1", "metadata": {"source": "dashboard", "plan_mode": True}}
    )
    assert summary["planMode"] is True

    summary_off = thread_api._thread_summary(
        {"thread_id": "t2", "metadata": {"source": "dashboard"}}
    )
    assert summary_off["planMode"] is False


def test_plan_mode_guidance_section_always_present() -> None:
    """The guidance section telling the agent about enter_plan_mode should be in every prompt."""
    prompt = construct_system_prompt(working_dir="/work", plan_mode=False)
    assert "enter_plan_mode" in prompt
    assert "Plan Mode" in prompt


def test_plan_mode_guidance_section_present_when_enabled() -> None:
    prompt = construct_system_prompt(working_dir="/work", plan_mode=True)
    assert "enter_plan_mode" in prompt
    assert "Plan Mode (ACTIVE)" in prompt


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
    assert result.update["plan_mode"] is True
    messages = result.update["messages"]
    assert len(messages) == 1
    assert isinstance(messages[0], ToolMessage)
    assert messages[0].tool_call_id == "call-1"


def test_enter_plan_mode_exported() -> None:
    from agent.tools import enter_plan_mode

    assert callable(enter_plan_mode)


@pytest.mark.parametrize(
    "reply",
    [
        "approve",
        "Approved!",
        "Looks good to me.",
        "go ahead",
        "ship it",
        "yes",
    ],
)
def test_natural_language_plan_approval_accepts_affirmative_replies(reply: str) -> None:
    from agent.webapp import _is_natural_language_plan_approval

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
    from agent.webapp import _is_natural_language_plan_approval

    assert _is_natural_language_plan_approval(reply) is False


def test_plan_mode_prompt_uses_plain_text_slack_approval() -> None:
    prompt = construct_system_prompt(working_dir="/work", plan_mode=True)

    assert "reply naturally in the thread" in prompt
    assert "do not use Block Kit or approval buttons" in prompt
