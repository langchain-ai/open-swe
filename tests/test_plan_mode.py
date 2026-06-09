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


def test_start_agent_run_passes_plan_mode_when_enabled(
    dashboard_run_client: _FakeLangGraphClient,
) -> None:
    asyncio.run(
        thread_api._start_agent_run(
            "thread-id",
            login="octo",
            repo_config={"owner": "octo", "name": "repo"},
            prompt="do work",
            plan_mode=True,
        )
    )

    configurable = dashboard_run_client.runs.configurable
    assert configurable is not None
    assert configurable["plan_mode"] is True


def test_start_agent_run_omits_plan_mode_when_disabled(
    dashboard_run_client: _FakeLangGraphClient,
) -> None:
    asyncio.run(
        thread_api._start_agent_run(
            "thread-id",
            login="octo",
            repo_config={"owner": "octo", "name": "repo"},
            prompt="do work",
        )
    )

    configurable = dashboard_run_client.runs.configurable
    assert configurable is not None
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
