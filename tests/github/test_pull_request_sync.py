"""PostgreSQL regressions for the GitHub to database pull request sync."""

from typing import Any

import httpx2
import pytest

from agent.github import pull_request_sync
from agent.github.pull_requests import PullRequest

pytestmark = pytest.mark.usefixtures("registry_db")

HEAD_SHA = "a" * 40
BASE_SHA = "b" * 40

PULL_REQUEST = {
    "number": 7,
    "title": "Add widget",
    "state": "open",
    "draft": False,
    "merged": False,
    "mergeable_state": "clean",
    "merged_at": None,
    "closed_at": None,
    "updated_at": "2026-02-01T00:00:00Z",
    "user": {"login": "ada", "id": 42},
    "head": {"ref": "feature", "sha": HEAD_SHA},
    "base": {"ref": "main", "sha": BASE_SHA, "repo": {"private": True}},
}

CHECK_RUNS = {
    "check_runs": [
        {
            "id": 1,
            "name": "lint",
            "status": "completed",
            "conclusion": "failure",
            "details_url": "https://ci/lint",
            "html_url": "https://ci/lint-html",
            "completed_at": "2026-02-01T00:01:00Z",
        },
        {"id": 2, "name": "build", "status": "in_progress", "conclusion": None, "html_url": None},
    ]
}

COMMIT_STATUSES = {
    "statuses": [
        {
            "context": "legacy/ci",
            "state": "error",
            "target_url": None,
            "updated_at": "2026-02-01T00:02:00Z",
        },
        {"context": "legacy/ci", "state": "success", "updated_at": "2026-01-01T00:00:00Z"},
    ]
}

REVIEW_THREADS = {
    "data": {
        "repository": {
            "pullRequest": {
                "reviewThreads": {
                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                    "nodes": [
                        {
                            "id": "RT_1",
                            "isResolved": False,
                            "path": "agent/app.py",
                            "line": None,
                            "originalLine": 12,
                            "comments": {
                                "nodes": [
                                    {
                                        "author": {"login": "bob"},
                                        "body": "needs a test",
                                        "url": "https://gh/c1",
                                    }
                                ]
                            },
                        },
                        {
                            "id": "RT_2",
                            "isResolved": True,
                            "path": "agent/other.py",
                            "line": 3,
                            "comments": {"nodes": [{"author": None, "body": "", "url": None}]},
                        },
                    ],
                }
            }
        }
    }
}


class _Response:
    def __init__(self, payload: object) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> object:
        return self._payload


def _install(monkeypatch: pytest.MonkeyPatch, *, failing: str = "") -> None:
    """Route every GitHub call to a canned payload; ``failing`` raises instead."""

    async def fake_request(_client: object, _method: str, url: str, **_kwargs: Any) -> _Response:
        if failing and failing in url:
            raise httpx2.HTTPError("github is down")
        if url.endswith("/check-runs"):
            return _Response(CHECK_RUNS)
        if url.endswith("/status"):
            return _Response(COMMIT_STATUSES)
        if url.endswith("/graphql"):
            return _Response(REVIEW_THREADS)
        if url.endswith("/pulls"):
            return _Response([{"number": 7}])
        return _Response(PULL_REQUEST)

    monkeypatch.setattr(pull_request_sync, "github_request", fake_request)


async def test_sync_stores_the_pull_request_its_checks_and_its_review_threads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install(monkeypatch)

    row = await pull_request_sync.sync_pull_request("lc", "repo", 7, token="t")

    assert row is not None
    assert (row.title, row.state, row.head_sha, row.base_sha) == (
        "Add widget",
        "open",
        HEAD_SHA,
        BASE_SHA,
    )
    assert (row.mergeable_state, row.author, row.author_github_id) == ("clean", "ada", 42)
    assert row.github_updated_at is not None and row.last_synced_at is not None
    assert {(check.kind, check.external_id, check.conclusion) for check in row.checks} == {
        ("check_run", "1", "failure"),
        ("check_run", "2", ""),
        ("status", "legacy/ci", "error"),
    }
    assert {failure["name"] for failure in row.check_summary().failing} == {"lint", "legacy/ci"}
    assert row.check_summary().pending == 1
    assert row.check_state == "failing"
    unresolved = row.unresolved_review_threads
    assert [(thread.node_id, thread.line, thread.author) for thread in unresolved] == [
        ("RT_1", 12, "bob")
    ]
    assert {thread.node_id for thread in row.review_threads} == {"RT_1", "RT_2"}


async def test_a_failed_check_read_keeps_the_stored_checks_and_still_syncs_threads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install(monkeypatch)
    await pull_request_sync.sync_pull_request("lc", "repo", 7, token="t")
    _install(monkeypatch, failing="/check-runs")

    row = await pull_request_sync.sync_pull_request("lc", "repo", 7, token="t")

    assert row is not None
    assert len(row.checks) == 3
    assert {thread.node_id for thread in row.review_threads} == {"RT_1", "RT_2"}


async def test_an_unreadable_pull_request_stores_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, failing="/pulls/7")

    assert await pull_request_sync.sync_pull_request("lc", "repo", 7, token="t") is None
    assert await PullRequest.get("lc", "repo", 7) is None


async def test_syncing_a_repository_walks_its_open_pull_requests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install(monkeypatch)

    rows = await pull_request_sync.sync_repository_open_pull_requests("lc", "repo", token="t")

    assert [row.number for row in rows] == [7]
