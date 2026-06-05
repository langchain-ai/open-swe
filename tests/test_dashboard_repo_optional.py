from __future__ import annotations

import asyncio
from typing import Any

import pytest

from agent.dashboard import thread_api


def test_resolve_repo_config_parses_request_repo() -> None:
    assert thread_api._resolve_repo_config("octo/repo") == {"owner": "octo", "name": "repo"}


def test_resolve_repo_config_returns_empty_when_no_repo_given() -> None:
    assert thread_api._resolve_repo_config(None) == {}
    assert thread_api._resolve_repo_config("") == {}
    assert thread_api._resolve_repo_config("not-a-repo") == {}


def test_thread_summary_blanks_repo_when_absent() -> None:
    summary = thread_api._thread_summary(
        {"thread_id": "t1", "metadata": {"source": "dashboard", "title": "no repo run"}}
    )
    assert summary["repo"] == ""
    assert summary["repoFullName"] == ""


def test_thread_summary_keeps_repo_when_present() -> None:
    summary = thread_api._thread_summary(
        {
            "thread_id": "t2",
            "metadata": {
                "source": "dashboard",
                "title": "repo run",
                "repo_owner": "octo",
                "repo_name": "repo",
            },
        }
    )
    assert summary["repo"] == "repo"
    assert summary["repoFullName"] == "octo/repo"


class _FakeThreadsClient:
    async def create(
        self, *, thread_id: str, metadata: dict[str, Any], if_exists: str
    ) -> dict[str, Any]:
        return {"thread_id": thread_id, "metadata": metadata, "if_exists": if_exists}

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


def test_start_agent_run_omits_repo_less_marker_when_repo_unset(
    dashboard_run_client: _FakeLangGraphClient,
) -> None:
    asyncio.run(
        thread_api._start_agent_run(
            "thread-id",
            login="octo",
            repo_config={},
            prompt="do work",
        )
    )

    configurable = dashboard_run_client.runs.configurable
    assert configurable is not None
    assert "repo_explicitly_none" not in configurable
    assert "repo" not in configurable


def test_start_agent_run_marks_repo_less_config_when_explicit(
    dashboard_run_client: _FakeLangGraphClient,
) -> None:
    asyncio.run(
        thread_api._start_agent_run(
            "thread-id",
            login="octo",
            repo_config={},
            repo_explicitly_none=True,
            prompt="do work",
        )
    )

    configurable = dashboard_run_client.runs.configurable
    assert configurable is not None
    assert configurable["repo_explicitly_none"] is True
    assert "repo" not in configurable


def test_start_agent_run_omits_repo_less_marker_when_repo_configured(
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
    assert configurable["repo"] == {"owner": "octo", "name": "repo"}
    assert "repo_explicitly_none" not in configurable
