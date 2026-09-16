import asyncio
from typing import Any

import pytest
from fastapi import HTTPException

from agent.threads import runs as thread_runs
from agent.threads import summary as thread_summary
from tests.conftest import patch_thread_module
from tests.support.repositories import FakeRepositories


async def _fake_trace_url(thread_id: str, **kwargs: object) -> str:
    return f"https://smith.example/t/{thread_id}"


async def test_resolve_repositories_accepts_either_request_field(
    fake_repositories: FakeRepositories,
) -> None:
    single = await thread_runs._resolve_repositories("octo/repo", None)
    assert [row.full_name for row in single] == ["octo/repo"]
    listed = await thread_runs._resolve_repositories(None, ["octo/one", "octo/two"])
    assert [row.full_name for row in listed] == ["octo/one", "octo/two"]
    both = await thread_runs._resolve_repositories("octo/one", ["octo/one", "octo/two"])
    assert [row.full_name for row in both] == ["octo/one", "octo/two"]


async def test_resolve_repositories_returns_empty_when_no_repo_given(
    fake_repositories: FakeRepositories,
) -> None:
    assert await thread_runs._resolve_repositories(None, None) == []
    assert await thread_runs._resolve_repositories("", []) == []


async def test_resolve_repositories_rejects_a_malformed_name(
    fake_repositories: FakeRepositories,
) -> None:
    with pytest.raises(HTTPException) as caught:
        await thread_runs._resolve_repositories("not-a-repo", None)
    assert caught.value.status_code == 400


async def test_thread_summary_has_no_repos_when_absent() -> None:
    summary = await thread_summary._thread_summary(
        {"thread_id": "t1", "metadata": {"source": "dashboard", "title": "no repo run"}}
    )
    assert summary["repos"] == []


async def test_thread_summary_lists_every_repo(fake_repositories: FakeRepositories) -> None:
    ids = [
        str(fake_repositories.add("octo/repo").id),
        str(fake_repositories.add("octo/other").id),
    ]
    summary = await thread_summary._thread_summary(
        {
            "thread_id": "t2",
            "metadata": {
                "source": "dashboard",
                "title": "repo run",
                "repository_ids": ids,
            },
        }
    )
    assert summary["repos"] == ["octo/repo", "octo/other"]


async def test_thread_summary_falls_back_to_legacy_single_repo_metadata(
    fake_repositories: FakeRepositories,
) -> None:
    summary = await thread_summary._thread_summary(
        {
            "thread_id": "t3",
            "metadata": {
                "source": "dashboard",
                "title": "repo run",
                "repo_owner": "octo",
                "repo_name": "repo",
            },
        }
    )
    assert summary["repos"] == ["octo/repo"]


async def test_thread_summary_classifies_legacy_schedule_metadata() -> None:
    summary = await thread_summary._thread_summary(
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
    assert summary["automationActionPosted"] is False


async def test_thread_summary_marks_automation_actions_posted_to_slack() -> None:
    summary = await thread_summary._thread_summary(
        {
            "thread_id": "scheduled-action",
            "metadata": {
                "source": "schedule",
                "schedule_id": "schedule-1",
                "automation_action_posted_at": "2026-08-21T12:00:00+00:00",
            },
        }
    )

    assert summary["automationActionPosted"] is True


async def test_thread_summary_derives_issue_category_from_source_context() -> None:
    summary = await thread_summary._thread_summary(
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
    patch_thread_module(monkeypatch, "get_langsmith_trace_url", _fake_trace_url)
    summary = await thread_summary._thread_summary(
        {"thread_id": "t3", "metadata": {"source": "dashboard", "title": "traced run"}}
    )
    assert summary["traceUrl"] == "https://smith.example/t/t3"


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

    patch_thread_module(monkeypatch, "langgraph_client", lambda: client)
    patch_thread_module(monkeypatch, "get_profile", fake_get_profile)
    patch_thread_module(monkeypatch, "_ensure_dashboard_github_token", fake_ensure_token)
    patch_thread_module(monkeypatch, "resolve_run_email", fake_resolve_email)
    return client


def test_create_thread_record_omits_repo_less_marker_when_repo_unset(
    dashboard_run_client: _FakeLangGraphClient,
    fake_repositories: FakeRepositories,
) -> None:
    # Runs now start client-side via the stream commands endpoint, so the run
    # configurable is assembled from thread metadata by
    # ``_build_dashboard_configurable``. The thread record must not persist a
    # repo-less marker when the repo is simply unset (not explicitly cleared).
    asyncio.run(
        thread_runs._create_dashboard_thread_record(
            "thread-id",
            login="octo",
            repositories=[],
            prompt="do work",
        )
    )

    configurable = asyncio.run(
        thread_runs._build_dashboard_configurable("thread-id", "octo", {"source": "dashboard"})
    )
    assert "repo_explicitly_none" not in configurable
    assert "repository_ids" not in configurable


def test_build_configurable_marks_repo_less_config_when_explicit(
    dashboard_run_client: _FakeLangGraphClient,
    fake_repositories: FakeRepositories,
) -> None:
    configurable = asyncio.run(
        thread_runs._build_dashboard_configurable(
            "thread-id",
            "octo",
            {"source": "dashboard", "repo_explicitly_none": True},
        )
    )
    assert configurable["repo_explicitly_none"] is True
    assert "repository_ids" not in configurable


def test_build_configurable_includes_every_repo_when_configured(
    dashboard_run_client: _FakeLangGraphClient,
    fake_repositories: FakeRepositories,
) -> None:
    ids = [
        str(fake_repositories.add("octo/repo").id),
        str(fake_repositories.add("octo/other").id),
    ]
    configurable = asyncio.run(
        thread_runs._build_dashboard_configurable(
            "thread-id",
            "octo",
            {"source": "dashboard", "repository_ids": ids},
        )
    )
    assert configurable["repository_ids"] == ids
    assert "repo_explicitly_none" not in configurable


def test_build_configurable_reads_legacy_single_repo_metadata(
    dashboard_run_client: _FakeLangGraphClient,
    fake_repositories: FakeRepositories,
) -> None:
    configurable = asyncio.run(
        thread_runs._build_dashboard_configurable(
            "thread-id",
            "octo",
            {"source": "dashboard", "repo_owner": "octo", "repo_name": "repo"},
        )
    )
    assert configurable["repository_ids"] == [str(fake_repositories.add("octo/repo").id)]
