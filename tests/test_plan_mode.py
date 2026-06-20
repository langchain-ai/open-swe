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
        "write_file",
        "edit_file",
        "task",
        "open_pull_request",
        "request_pr_review",
        "linear_create_issue",
        "linear_update_issue",
        "linear_delete_issue",
    ):
        assert tool in excluded
    # Read-only tools must stay available.
    assert "read_file" not in excluded
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


def test_enter_plan_mode_tool_returns_command() -> None:
    from langgraph.types import Command

    from agent.tools.enter_plan_mode import enter_plan_mode

    result = enter_plan_mode()
    assert isinstance(result, Command)
    assert result.update == {"plan_mode": True}


def test_enter_plan_mode_exported() -> None:
    from agent.tools import enter_plan_mode

    assert callable(enter_plan_mode)


def test_profile_plan_mode_default_false_when_missing() -> None:
    from agent.dashboard.agent_overrides import profile_plan_mode_default

    assert profile_plan_mode_default(None) is False
    assert profile_plan_mode_default({}) is False
    assert profile_plan_mode_default({"plan_mode_default": False}) is False


def test_profile_plan_mode_default_true_when_set() -> None:
    from agent.dashboard.agent_overrides import profile_plan_mode_default

    assert profile_plan_mode_default({"plan_mode_default": True}) is True


def test_profile_update_has_plan_mode_default() -> None:
    from agent.dashboard.profiles import ProfileUpdate

    update = ProfileUpdate(default_model="anthropic:claude-opus-4-8", reasoning_effort="high")
    assert update.plan_mode_default is False


def test_team_settings_update_has_plan_mode_default() -> None:
    from agent.dashboard.team_settings import TeamSettingsUpdate

    update = TeamSettingsUpdate()
    assert update.plan_mode_default is False


def test_team_default_settings_includes_plan_mode_default() -> None:
    from agent.dashboard.team_settings import _default_settings

    defaults = _default_settings()
    assert "plan_mode_default" in defaults
    assert defaults["plan_mode_default"] is False


def test_parse_plan_command_on() -> None:
    from agent.webapp import _parse_plan_command

    assert _parse_plan_command("plan on") == "on"
    assert _parse_plan_command("@bot plan on please") == "on"
    assert _parse_plan_command("PLAN ON") == "on"


def test_parse_plan_command_off() -> None:
    from agent.webapp import _parse_plan_command

    assert _parse_plan_command("plan off") == "off"
    assert _parse_plan_command("hey plan off now") == "off"


def test_parse_plan_command_status() -> None:
    from agent.webapp import _parse_plan_command

    assert _parse_plan_command("plan status") == "status"


def test_parse_plan_command_none_when_no_match() -> None:
    from agent.webapp import _parse_plan_command

    assert _parse_plan_command("build me a cli with a plan subcommand") is None
    assert _parse_plan_command("hello world") is None
    assert _parse_plan_command("") is None


def test_build_plan_approval_blocks_has_three_buttons() -> None:
    from agent.tools.slack_thread_reply import _build_plan_approval_blocks

    blocks = _build_plan_approval_blocks("Here is my plan")
    assert len(blocks) == 2
    assert blocks[0]["type"] == "section"
    actions = blocks[1]
    assert actions["type"] == "actions"
    elements = actions["elements"]
    assert len(elements) == 3
    texts = [e["text"]["text"] for e in elements]
    assert "Approve & Implement" in texts
    assert "Revise Plan" in texts
    assert "Cancel" in texts


def test_build_plan_approval_blocks_values_have_plan_approval_type() -> None:
    import json

    from agent.tools.slack_thread_reply import _build_plan_approval_blocks

    blocks = _build_plan_approval_blocks("plan text")
    for element in blocks[1]["elements"]:
        value = json.loads(element["value"])
        assert value["type"] == "plan_approval"
        assert value["action"] in ("approve", "revise", "cancel")
