"""How a thread's metadata becomes the summary the Agents UI lists and renders.

Everything here goes through a public handler (``get_dashboard_thread``,
``list_dashboard_threads*``, ``admin_cancel_dashboard_thread``) rather than the
summary builder itself, so that moving that builder does not move these tests.
"""

from typing import Any, cast

import pytest
from fastapi import HTTPException
from support.langgraph_fakes import FakeLangGraphClient

from agent.dashboard import authz
from agent.dashboard.threads import listing as thread_listing
from agent.dashboard.threads import runs as thread_runs

# The columns the list endpoints ask the platform for, and the page size they
# scan it in -- paging is only observable once the seeded threads exceed it.
_LIST_SELECT = ["thread_id", "status", "metadata", "updated_at"]
_SEARCH_PAGE = 500


def _patch_client(monkeypatch, client: FakeLangGraphClient) -> FakeLangGraphClient:
    for module in (authz, thread_listing, thread_runs):
        monkeypatch.setattr(module, "langgraph_client", lambda: client)
    return client


def _install_client(monkeypatch, **kwargs: Any) -> FakeLangGraphClient:
    return _patch_client(monkeypatch, FakeLangGraphClient(**kwargs))


async def _summary(
    monkeypatch,
    metadata: dict[str, Any],
    *,
    login: str = "octocat",
    email: str | None = None,
) -> dict[str, Any]:
    _install_client(
        monkeypatch, threads=[{"thread_id": "tid", "status": "idle", "metadata": metadata}]
    )
    return await thread_listing.get_dashboard_thread("tid", login, email=email)


async def test_summary_includes_pr_and_diff_stats(monkeypatch) -> None:
    summary = await _summary(
        monkeypatch,
        {
            "repo_full_name": "langchain-ai/open-swe",
            "title": "Add feature",
            "pr_number": 42,
            "pr_url": "https://github.com/langchain-ai/open-swe/pull/42",
            "pr_state": "draft",
            "pr_title": "feat: add feature",
            "branch_name": "open-swe/feature",
            "base_branch": "main",
            "diff_stats": {"files": 3, "additions": 10, "deletions": 2},
        },
    )

    assert summary["pr"] == {
        "number": 42,
        "title": "feat: add feature",
        "state": "draft",
        "headRef": "open-swe/feature",
        "baseRef": "main",
        "url": "https://github.com/langchain-ai/open-swe/pull/42",
    }
    assert summary["diffStats"] == {"files": 3, "additions": 10, "deletions": 2}
    assert summary["pullRequests"][0]["repoFullName"] == "langchain-ai/open-swe"


async def test_summary_includes_pull_requests_across_repositories(monkeypatch) -> None:
    summary = await _summary(
        monkeypatch,
        {
            "repo_full_name": "langchain-ai/open-swe",
            "title": "Cross-repo change",
            "pull_requests": [
                {
                    "repo_full_name": "langchain-ai/open-swe",
                    "number": 42,
                    "url": "https://github.com/langchain-ai/open-swe/pull/42",
                    "title": "feat: dashboard",
                    "state": "draft",
                    "head_ref": "feature/dashboard",
                    "base_ref": "main",
                    "author": "octocat",
                    "author_avatar_url": "https://avatars.example/octocat.png",
                    "created_at": "2026-08-18T10:00:00Z",
                    "diff_stats": {"files": 3, "additions": 10, "deletions": 2},
                },
                {
                    "repo_full_name": "langchain-ai/langchain",
                    "number": 9,
                    "url": "https://github.com/langchain-ai/langchain/pull/9",
                    "title": "feat: integration",
                    "state": "open",
                    "head_ref": "feature/integration",
                    "base_ref": "master",
                    "author": "hubot",
                    "diff_stats": {"files": 1, "additions": 4, "deletions": 0},
                },
            ],
        },
    )

    assert [item["repoFullName"] for item in summary["pullRequests"]] == [
        "langchain-ai/open-swe",
        "langchain-ai/langchain",
    ]
    assert summary["pr"] == {
        "number": 9,
        "title": "feat: integration",
        "state": "open",
        "headRef": "feature/integration",
        "baseRef": "master",
        "url": "https://github.com/langchain-ai/langchain/pull/9",
    }
    assert summary["diffStats"] == {"files": 1, "additions": 4, "deletions": 0}


async def test_summary_uses_configured_repo_for_display(monkeypatch) -> None:
    metadata: dict[str, Any] = {
        "repo": {"owner": "trusted", "name": "default"},
        "working_repo_full_name": "observed/checkout",
    }

    summary = await _summary(monkeypatch, metadata)

    assert summary["repo"] == "default"
    assert summary["repoFullName"] == "trusted/default"
    assert "workingRepoFullName" not in summary
    assert metadata["repo"] == {"owner": "trusted", "name": "default"}


async def test_summary_defaults_unknown_pr_state_to_open(monkeypatch) -> None:
    summary = await _summary(
        monkeypatch,
        {"pr_number": 7, "pr_url": "https://example.com/pull/7", "pr_state": "bogus"},
    )

    assert summary["pr"]["state"] == "open"


async def test_summary_omits_pr_when_no_pr_metadata(monkeypatch) -> None:
    summary = await _summary(monkeypatch, {"title": "No PR"})

    assert "pr" not in summary
    assert "diffStats" not in summary


async def test_summary_exposes_sandbox_id(monkeypatch) -> None:
    summary = await _summary(monkeypatch, {"sandbox_id": "sb-abc123"})

    assert summary["sandboxId"] == "sb-abc123"


async def test_summary_hides_creating_sandbox_sentinel(monkeypatch) -> None:
    summary = await _summary(monkeypatch, {"sandbox_id": "__creating__"})

    assert summary["sandboxId"] is None


async def test_summary_includes_slack_source_url_for_private_repo(monkeypatch) -> None:
    summary = await _summary(
        monkeypatch,
        {
            "source": "slack",
            "repo_private": True,
            "source_context": {"slack_thread": {"permalink": "https://slack.example/thread"}},
        },
    )

    assert summary["sourceUrl"] == "https://slack.example/thread"


async def test_summary_omits_slack_source_url_for_public_repo(monkeypatch) -> None:
    summary = await _summary(
        monkeypatch,
        {
            "source": "slack",
            "repo_private": False,
            "source_context": {"slack_thread": {"permalink": "https://slack.example/thread"}},
        },
    )

    assert summary["sourceUrl"] is None


async def test_summary_exposes_resolved_state(monkeypatch) -> None:
    summary = await _summary(
        monkeypatch,
        {
            "source": "dashboard",
            "github_login": "octocat",
            "resolved": True,
            "resolved_at_ms": 1700,
        },
    )

    assert summary["resolved"] is True
    assert summary["resolvedAt"] == 1700


async def test_summary_defaults_to_not_resolved(monkeypatch) -> None:
    summary = await _summary(monkeypatch, {"source": "dashboard", "github_login": "octocat"})

    assert summary["resolved"] is False
    assert summary["resolvedAt"] is None


async def test_summary_is_owner_true_for_matching_login(monkeypatch) -> None:
    summary = await _summary(monkeypatch, {"source": "slack", "github_login": "octocat"})

    assert summary["isOwner"] is True


async def test_summary_is_owner_false_for_non_owner(monkeypatch) -> None:
    summary = await _summary(
        monkeypatch, {"source": "slack", "github_login": "octocat"}, login="teammate"
    )

    assert summary["isOwner"] is False


async def test_summary_is_owner_true_for_matching_email(monkeypatch) -> None:
    summary = await _summary(
        monkeypatch,
        {
            "source": "slack",
            "github_login": "octocat",
            "triggering_user_email": "octo@example.com",
        },
        login="someone-else",
        email="OCTO@example.com",
    )

    assert summary["isOwner"] is True


async def test_summary_is_owner_defaults_true_without_owner_login(monkeypatch) -> None:
    _install_client(
        monkeypatch,
        threads=[
            {
                "thread_id": "tid",
                "status": "busy",
                "metadata": {"source": "slack", "github_login": "octocat"},
            }
        ],
        runs={"tid": [{"run_id": "run-1", "status": "running"}]},
    )

    summary = await thread_runs.admin_cancel_dashboard_thread("tid")

    assert summary["isOwner"] is True


def _activity_client(
    metadata: dict[str, Any], run_status: str, *, thread_status: str = "idle"
) -> FakeLangGraphClient:
    return FakeLangGraphClient(
        threads=[{"thread_id": "tid", "status": thread_status, "metadata": dict(metadata)}],
        runs={"tid": [{"run_id": "run-1", "status": run_status}]},
    )


def _metadata(client: FakeLangGraphClient) -> dict[str, Any]:
    return client.threads.threads["tid"]["metadata"]


async def test_list_refreshes_finished_run_status(monkeypatch) -> None:
    client = _activity_client(
        {
            "source": "dashboard",
            "github_login": "octocat",
            "latest_run_id": "run-1",
            "latest_run_status": "pending",
        },
        "success",
    )
    _patch_client(monkeypatch, client)

    results = await thread_listing.list_dashboard_threads("octocat")

    assert results[0]["status"] == "finished"
    assert results[0]["viewed"] is False
    assert _metadata(client)["latest_run_status"] == "success"
    assert client.runs.list_calls == [{"thread_id": "tid", "limit": 1, "status": None}]


async def test_get_thread_marks_finished_thread_viewed(monkeypatch) -> None:
    client = _activity_client(
        {
            "source": "dashboard",
            "github_login": "octocat",
            "latest_run_id": "run-1",
            "latest_run_status": "success",
        },
        "success",
    )
    _patch_client(monkeypatch, client)

    result = await thread_listing.get_dashboard_thread("tid", "octocat")

    assert result["status"] == "finished"
    assert result["viewed"] is True
    assert isinstance(result["viewedAt"], int)
    assert _metadata(client)["last_viewed_run_id"] == "run-1"


async def test_get_thread_readable_by_non_owner(monkeypatch) -> None:
    client = _activity_client(
        {
            "source": "slack",
            "github_login": "octocat",
            "latest_run_id": "run-1",
            "latest_run_status": "success",
        },
        "success",
    )
    _patch_client(monkeypatch, client)

    result = await thread_listing.get_dashboard_thread("tid", "someone-else")

    assert result["status"] == "finished"
    assert "last_viewed_run_id" not in _metadata(client)


async def test_get_thread_skips_mark_viewed_when_disabled(monkeypatch) -> None:
    client = _activity_client(
        {
            "source": "dashboard",
            "github_login": "octocat",
            "latest_run_id": "run-1",
            "latest_run_status": "success",
        },
        "success",
    )
    _patch_client(monkeypatch, client)

    result = await thread_listing.get_dashboard_thread("tid", "octocat", mark_viewed=False)

    assert result["status"] == "finished"
    assert result["viewed"] is False
    assert "last_viewed_run_id" not in _metadata(client)


async def test_get_thread_does_not_mark_running_thread_viewed(monkeypatch) -> None:
    client = _activity_client(
        {
            "source": "dashboard",
            "github_login": "octocat",
            "latest_run_id": "run-1",
            "latest_run_status": "running",
        },
        "running",
        thread_status="busy",
    )
    _patch_client(monkeypatch, client)

    result = await thread_listing.get_dashboard_thread("tid", "octocat")

    assert result["status"] == "running"
    assert result["viewed"] is False
    assert "last_viewed_run_id" not in _metadata(client)


_FILTER_THREADS: list[dict[str, Any]] = [
    {
        "thread_id": "interactive",
        "status": "idle",
        "metadata": {
            "source": "dashboard",
            "github_login": "octocat",
            "title": "Fix login bug",
            "resolved": True,
            "latest_run_status": "success",
            "updated_at_ms": 2,
        },
    },
    {
        "thread_id": "automation",
        "status": "idle",
        "metadata": {
            "source": "schedule",
            "github_login": "octocat",
            "schedule_id": "schedule-1",
            "title": "Scheduled cleanup",
            "latest_run_status": "error",
            "updated_at_ms": 1,
        },
    },
]


async def _filtered_ids(**filters: Any) -> list[str]:
    page = await thread_listing.list_dashboard_threads_page("octocat", email=None, **filters)
    return [item["id"] for item in page["items"]]


async def test_list_page_filters_on_metadata(monkeypatch) -> None:
    _install_client(monkeypatch, threads=_FILTER_THREADS)

    assert await _filtered_ids(resolved=True) == ["interactive"]
    assert await _filtered_ids(resolved=False) == ["automation"]
    assert await _filtered_ids(source="dashboard", query="login") == ["interactive"]
    assert await _filtered_ids(source="github") == []
    assert await _filtered_ids(scope="interactive") == ["interactive"]
    assert await _filtered_ids(scope="automation", automation_id="schedule-1") == ["automation"]
    assert await _filtered_ids(scope="automation", automation_id="schedule-2") == []


async def test_list_page_filters_on_the_summary(monkeypatch) -> None:
    """``status`` and ``viewed`` are only knowable once the summary is built."""

    _install_client(monkeypatch, threads=_FILTER_THREADS)

    assert await _filtered_ids(status="finished") == ["interactive"]
    assert await _filtered_ids(status="error") == ["automation"]
    assert await _filtered_ids(viewed=False) == ["interactive", "automation"]
    assert await _filtered_ids(viewed=True) == []
    assert await _filtered_ids(status="finished", query="login") == ["interactive"]
    assert await _filtered_ids(status="finished", query="missing") == []


def _make_threads(count: int, *, resolved_before: int) -> list[dict[str, object]]:
    threads: list[dict[str, object]] = []
    for index in range(count):
        threads.append(
            {
                "thread_id": f"t{index}",
                "metadata": {
                    "source": "dashboard",
                    "github_login": "octocat",
                    "title": f"Thread {index}",
                    "updated_at_ms": count - index,
                    "resolved": index < resolved_before,
                },
            }
        )
    return threads


def _search_offsets(client: FakeLangGraphClient) -> list[int]:
    return [call["offset"] for call in client.threads.searches]


def _assert_list_select(client: FakeLangGraphClient) -> None:
    searches = client.threads.searches
    assert searches
    assert all(call["select"] == _LIST_SELECT for call in searches)


async def test_list_page_pages_beyond_first_search_batch(monkeypatch) -> None:
    threads = _make_threads(_SEARCH_PAGE + 50, resolved_before=_SEARCH_PAGE)
    for thread in threads:
        cast(dict[str, object], thread["metadata"])["latest_run_status"] = "success"
    client = _install_client(monkeypatch, threads=threads)

    result = await thread_listing.list_dashboard_threads_page(
        "octocat", email=None, limit=25, offset=0, resolved=False
    )

    assert result["hasMore"] is True
    assert len(result["items"]) == 25
    assert all(item["resolved"] is False for item in result["items"])
    _assert_list_select(client)
    assert _SEARCH_PAGE in _search_offsets(client)
    assert client.runs.list_calls == []


async def test_list_page_scopes_automation_runs(monkeypatch) -> None:
    threads = _make_threads(3, resolved_before=0)
    for thread in threads:
        cast(dict[str, object], thread["metadata"])["latest_run_status"] = "success"
    first = cast(dict[str, object], threads[0]["metadata"])
    first.update({"source": "schedule", "schedule_id": "schedule-1"})
    second = cast(dict[str, object], threads[1]["metadata"])
    second.update({"source": "schedule", "schedule_id": "schedule-2"})
    threads.append(
        {
            "thread_id": "other-owner",
            "metadata": {
                "source": "schedule",
                "github_login": "someone-else",
                "schedule_id": "schedule-1",
                "latest_run_status": "success",
                "updated_at_ms": 10,
            },
        }
    )

    _install_client(monkeypatch, threads=threads)

    interactive = await thread_listing.list_dashboard_threads_page(
        "octocat", email=None, scope="interactive"
    )
    automation = await thread_listing.list_dashboard_threads_page(
        "octocat", email=None, scope="automation", automation_id="schedule-1"
    )

    assert [item["id"] for item in interactive["items"]] == ["t2"]
    assert [item["id"] for item in automation["items"]] == ["t0"]
    assert automation["items"][0]["automationId"] == "schedule-1"


async def test_list_page_separates_filter_owner_from_viewer(monkeypatch) -> None:
    threads = [
        {
            "thread_id": "surfaced",
            "metadata": {
                "source": "dashboard",
                "github_login": "other-user",
                "latest_run_status": "success",
                "updated_at_ms": 2,
            },
        },
        {
            "thread_id": "internal",
            "metadata": {
                "source": "reviewer",
                "github_login": "other-user",
                "latest_run_status": "success",
                "updated_at_ms": 1,
            },
        },
    ]
    client = _install_client(monkeypatch, threads=threads)

    result = await thread_listing.list_dashboard_threads_page(
        "admin-user",
        email="admin@example.com",
        filter_owner_login="other-user",
        surfaced_only=True,
    )

    assert [call["metadata"] for call in client.threads.searches] == [
        {"github_login": "other-user"}
    ]
    assert [item["id"] for item in result["items"]] == ["surfaced"]
    assert result["items"][0]["ownerLogin"] == "other-user"
    assert result["items"][0]["isOwner"] is False


async def test_list_sidebar_fills_buckets_with_one_endpoint(monkeypatch) -> None:
    threads = _make_threads(_SEARCH_PAGE + 10, resolved_before=_SEARCH_PAGE)
    client = _install_client(monkeypatch, threads=threads)

    result = await thread_listing.list_dashboard_threads_sidebar(
        "octocat", email=None, active_limit=5, resolved_limit=5
    )

    assert len(result["active"]["items"]) == 5
    assert len(result["resolved"]["items"]) == 5
    assert result["active"]["hasMore"] is True
    assert result["resolved"]["hasMore"] is True
    _assert_list_select(client)
    assert set(_search_offsets(client)) == {0, _SEARCH_PAGE}


async def test_list_sidebar_excludes_automations_before_limiting(monkeypatch) -> None:
    threads = _make_threads(_SEARCH_PAGE + 5, resolved_before=0)
    for thread in threads[:_SEARCH_PAGE]:
        metadata = cast(dict[str, object], thread["metadata"])
        metadata["source"] = "schedule"
        metadata["schedule_id"] = f"schedule-{thread['thread_id']}"
    client = _install_client(monkeypatch, threads=threads)

    result = await thread_listing.list_dashboard_threads_sidebar(
        "octocat", email=None, active_limit=5, resolved_limit=5
    )

    assert [item["id"] for item in result["active"]["items"]] == [
        f"t{index}" for index in range(_SEARCH_PAGE, _SEARCH_PAGE + 5)
    ]
    assert set(_search_offsets(client)) == {0, _SEARCH_PAGE}


async def test_list_sidebar_includes_readable_active_thread(monkeypatch) -> None:
    threads = _make_threads(1, resolved_before=0)
    shared_thread = {
        "thread_id": "shared-thread",
        "metadata": {
            "source": "slack",
            "github_login": "teammate",
            "title": "Teammate thread",
            "updated_at_ms": 100,
            "latest_run_status": "success",
            "sandbox_id": "sandbox-123",
        },
    }
    client = _install_client(monkeypatch, threads=threads)
    # Fetchable by id, but not one of the viewer's own threads that search returns.
    client.threads.threads["shared-thread"] = shared_thread

    result = await thread_listing.list_dashboard_threads_sidebar(
        "octocat",
        email=None,
        active_limit=5,
        resolved_limit=5,
        active_thread_id="shared-thread",
    )

    assert [item["id"] for item in result["active"]["items"]] == ["shared-thread", "t0"]
    shared = result["active"]["items"][0]
    assert shared["isOwner"] is False
    assert shared["sandboxId"] == "sandbox-123"
    _assert_list_select(client)


async def test_list_sidebar_keeps_resolved_active_thread_resolved(monkeypatch) -> None:
    threads = _make_threads(1, resolved_before=0)
    shared_thread = {
        "thread_id": "shared-resolved-thread",
        "metadata": {
            "source": "slack",
            "github_login": "teammate",
            "title": "Resolved teammate thread",
            "updated_at_ms": 100,
            "latest_run_status": "success",
            "resolved": True,
            "sandbox_id": "sandbox-456",
        },
    }
    client = _install_client(monkeypatch, threads=threads)
    client.threads.threads["shared-resolved-thread"] = shared_thread

    result = await thread_listing.list_dashboard_threads_sidebar(
        "octocat",
        email=None,
        active_limit=5,
        resolved_limit=5,
        active_thread_id="shared-resolved-thread",
    )

    assert [item["id"] for item in result["active"]["items"]] == ["t0"]
    assert [item["id"] for item in result["resolved"]["items"]] == ["shared-resolved-thread"]
    shared = result["resolved"]["items"][0]
    assert shared["isOwner"] is False
    assert shared["resolved"] is True
    assert shared["sandboxId"] == "sandbox-456"
    _assert_list_select(client)


async def test_list_sidebar_ignores_unreadable_active_thread(monkeypatch) -> None:
    threads = _make_threads(1, resolved_before=0)
    private_thread = {
        "thread_id": "private-thread",
        "metadata": {
            "source": "internal",
            "github_login": "teammate",
            "title": "Private thread",
            "updated_at_ms": 100,
            "latest_run_status": "success",
        },
    }

    client = _install_client(monkeypatch, threads=threads)
    client.threads.threads["private-thread"] = private_thread

    result = await thread_listing.list_dashboard_threads_sidebar(
        "octocat",
        email=None,
        active_limit=5,
        resolved_limit=5,
        active_thread_id="private-thread",
    )

    assert [item["id"] for item in result["active"]["items"]] == ["t0"]
    _assert_list_select(client)


async def test_list_page_refreshes_only_unsettled_threads(monkeypatch) -> None:
    threads = _make_threads(3, resolved_before=0)
    cast(dict[str, object], threads[0]["metadata"])["latest_run_status"] = "success"
    cast(dict[str, object], threads[1]["metadata"])["latest_run_status"] = "pending"
    cast(dict[str, object], threads[2]["metadata"])["latest_run_status"] = "error"
    client = _install_client(
        monkeypatch, threads=threads, runs=[{"id": "run-1", "status": "success"}]
    )

    result = await thread_listing.list_dashboard_threads_page(
        "octocat", email=None, limit=3, offset=0
    )

    assert [call["thread_id"] for call in client.runs.list_calls] == ["t1"]
    assert client.threads.update_calls == [
        {
            "thread_id": "t1",
            "metadata": {"latest_run_status": "success", "latest_run_id": "run-1"},
        }
    ]
    assert [item["status"] for item in result["items"]] == ["finished", "finished", "error"]


async def test_status_filter_refreshes_threads_missing_run_status(monkeypatch) -> None:
    threads = _make_threads(2, resolved_before=0)
    for thread in threads:
        cast(dict[str, object], thread["metadata"])["source"] = "slack"
    client = _install_client(
        monkeypatch,
        threads=threads,
        runs={
            "t0": [{"id": "run-t0", "status": "success"}],
            "t1": [{"id": "run-t1", "status": "error"}],
        },
    )

    result = await thread_listing.list_dashboard_threads_page(
        "octocat", email=None, limit=25, offset=0, status="finished"
    )

    assert {item["id"] for item in result["items"]} == {"t0"}
    assert result["items"][0]["status"] == "finished"
    assert {call["thread_id"] for call in client.runs.list_calls} == {"t0", "t1"}


async def test_resolve_thread_marks_resolved(monkeypatch) -> None:
    client = _install_client(
        monkeypatch, thread_metadata={"source": "dashboard", "github_login": "octocat"}
    )

    summary = await thread_runs.resolve_dashboard_thread("tid", "octocat", resolved=True)

    updates = client.threads.updates
    assert updates[-1]["resolved"] is True
    assert isinstance(updates[-1]["resolved_at_ms"], int)
    assert summary["resolved"] is True


async def test_resolve_thread_clears_resolved(monkeypatch) -> None:
    client = _install_client(
        monkeypatch,
        thread_metadata={
            "source": "dashboard",
            "github_login": "octocat",
            "resolved": True,
            "resolved_at_ms": 1700,
        },
    )

    summary = await thread_runs.resolve_dashboard_thread("tid", "octocat", resolved=False)

    updates = client.threads.updates
    assert updates[-1]["resolved"] is False
    assert updates[-1]["resolved_at_ms"] is None
    assert summary["resolved"] is False


async def test_resolve_thread_enforces_ownership(monkeypatch) -> None:
    _install_client(monkeypatch, thread_metadata={"source": "dashboard", "github_login": "owner"})

    with pytest.raises(HTTPException) as exc_info:
        await thread_runs.resolve_dashboard_thread("tid", "intruder", resolved=True)
    assert exc_info.value.status_code == 404
