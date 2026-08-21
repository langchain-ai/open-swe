import asyncio
from typing import Any

import pytest
from support.langgraph_fakes import FakeLangGraphClient

from agent.dashboard.threads import runs as thread_runs
from agent.dashboard.threads import serialize as thread_serialize


async def _fake_trace_url(thread_id: str, **kwargs: object) -> str:
    return f"https://smith.example/t/{thread_id}"


def test_resolve_repo_config_parses_request_repo() -> None:
    assert thread_runs.resolve_repo_config("octo/repo") == {"owner": "octo", "name": "repo"}


def test_resolve_repo_config_returns_empty_when_no_repo_given() -> None:
    assert thread_runs.resolve_repo_config(None) == {}
    assert thread_runs.resolve_repo_config("") == {}
    assert thread_runs.resolve_repo_config("not-a-repo") == {}


async def test_thread_summary_blanks_repo_when_absent() -> None:
    summary = await thread_serialize.thread_summary(
        {"thread_id": "t1", "metadata": {"source": "dashboard", "title": "no repo run"}}
    )
    assert summary["repo"] == ""
    assert summary["repoFullName"] == ""


async def test_thread_summary_keeps_repo_when_present() -> None:
    summary = await thread_serialize.thread_summary(
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


async def test_thread_summary_classifies_legacy_schedule_metadata() -> None:
    summary = await thread_serialize.thread_summary(
        {
            "thread_id": "scheduled",
            "metadata": {
                "source": "schedule",
                "schedule_id": "schedule-1",
                "schedule_name": "Daily triage",
            },
        }
    )
    assert summary["origin"] == "schedule"
    assert summary["threadCategory"] == "automation"
    assert summary["triggerKind"] == "schedule"
    assert summary["automationId"] == "schedule-1"
    assert summary["automationName"] == "Daily triage"


async def test_thread_summary_derives_issue_category_from_source_context() -> None:
    summary = await thread_serialize.thread_summary(
        {
            "thread_id": "linear",
            "metadata": {
                "source": "linear",
                "source_context": {"linear_issue": {"id": "issue-1"}},
            },
        }
    )
    assert summary["origin"] == "linear"
    assert summary["threadCategory"] == "issue"
    assert summary["triggerKind"] == "user"


async def test_thread_summary_includes_trace_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        thread_serialize,
        "get_langsmith_trace_url",
        _fake_trace_url,
    )
    summary = await thread_serialize.thread_summary(
        {"thread_id": "t3", "metadata": {"source": "dashboard", "title": "traced run"}}
    )
    assert summary["traceUrl"] == "https://smith.example/t/t3"


@pytest.fixture
def dashboard_run_client(monkeypatch: pytest.MonkeyPatch) -> FakeLangGraphClient:
    client = FakeLangGraphClient(thread_metadata={})

    async def fake_get_profile(login: str) -> dict[str, Any]:
        return {}

    async def fake_ensure_token(login: str) -> None:
        return None

    async def fake_resolve_email(login: str, profile: dict[str, Any]) -> str:
        return "octo@example.com"

    monkeypatch.setattr(thread_runs, "langgraph_client", lambda: client)
    monkeypatch.setattr(thread_runs, "get_profile", fake_get_profile)
    monkeypatch.setattr(thread_runs, "_ensure_dashboard_github_token", fake_ensure_token)
    monkeypatch.setattr(thread_runs, "resolve_run_email", fake_resolve_email)
    return client


def test_create_thread_record_omits_repo_less_marker_when_repo_unset(
    dashboard_run_client: FakeLangGraphClient,
) -> None:
    # Runs now start client-side via the stream commands endpoint, so the run
    # configurable is assembled from thread metadata by
    # ``build_thread_configurable``. The thread record must not persist a
    # repo-less marker when the repo is simply unset (not explicitly cleared).
    asyncio.run(
        thread_runs.create_dashboard_thread_record(
            "thread-id",
            login="octo",
            repo_config={},
            prompt="do work",
        )
    )

    configurable = asyncio.run(
        thread_runs.build_thread_configurable("thread-id", "octo", {"source": "dashboard"})
    )
    assert "repo_explicitly_none" not in configurable
    assert "repo" not in configurable


def test_build_configurable_marks_repo_less_config_when_explicit(
    dashboard_run_client: FakeLangGraphClient,
) -> None:
    configurable = asyncio.run(
        thread_runs.build_thread_configurable(
            "thread-id",
            "octo",
            {"source": "dashboard", "repo_explicitly_none": True},
        )
    )
    assert configurable["repo_explicitly_none"] is True
    assert "repo" not in configurable


def test_build_configurable_includes_repo_when_configured(
    dashboard_run_client: FakeLangGraphClient,
) -> None:
    configurable = asyncio.run(
        thread_runs.build_thread_configurable(
            "thread-id",
            "octo",
            {"source": "dashboard", "repo_owner": "octo", "repo_name": "repo"},
        )
    )
    assert configurable["repo"] == {"owner": "octo", "name": "repo"}
    assert "repo_explicitly_none" not in configurable
