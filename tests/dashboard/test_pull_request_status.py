from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import httpx2
import pytest
from fastapi import HTTPException

from agent.github import pull_request_status
from agent.github.pull_requests import PullRequest, PullRequestCheck, PullRequestReviewThread
from agent.threads import access as thread_access
from agent.threads import handlers
from tests.conftest import patch_thread_module


def _response(status: int, payload: object) -> httpx2.Response:
    return httpx2.Response(
        status, json=payload, request=httpx2.Request("GET", "https://api.github.com")
    )


@asynccontextmanager
async def _client(**kwargs):
    assert kwargs == {"token": "oauth-token"}
    yield object()


@pytest.fixture(autouse=True)
def write_backs(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str, int, str]]:
    """Capture background write-backs instead of letting them reach GitHub."""
    scheduled: list[tuple[str, str, int, str]] = []
    monkeypatch.setattr(
        pull_request_status,
        "schedule_pull_request_sync",
        lambda owner, repo, number, *, token: scheduled.append((owner, repo, number, token)),
    )
    return scheduled


@pytest.fixture(autouse=True)
def stored_rows(monkeypatch: pytest.MonkeyPatch) -> list[PullRequest]:
    """Stand in for the pull request table; empty unless a test fills it."""
    rows: list[PullRequest] = []
    monkeypatch.setattr(pull_request_status.postgres, "configured", lambda: True)
    monkeypatch.setattr(PullRequest, "get_all", AsyncMock(return_value=rows))
    return rows


def _stored_row(*, age: timedelta = timedelta(0)) -> PullRequest:
    row = PullRequest(
        owner="o",
        repo="r",
        number=7,
        state="draft",
        draft=True,
        head_sha="a" * 40,
        mergeable_state="dirty",
    )
    row.last_synced_at = datetime.now(UTC) - age
    row.checks = [
        PullRequestCheck(
            head_sha="a" * 40,
            kind="check_run",
            external_id="1",
            name="unit",
            status="completed",
            conclusion="timed_out",
            details_url="https://checks/unit",
        ),
        PullRequestCheck(
            head_sha="a" * 40, kind="check_run", external_id="2", name="deploy", status="queued"
        ),
        PullRequestCheck(
            head_sha="a" * 40,
            kind="check_run",
            external_id="3",
            name="lint",
            status="completed",
            conclusion="skipped",
        ),
    ]
    row.review_threads = [
        PullRequestReviewThread(
            node_id="t1",
            path="a.py",
            line=4,
            author="alice",
            body="fix this",
            url="https://github.com/o/r/pull/7#discussion_r1",
        ),
        PullRequestReviewThread(node_id="t2", is_resolved=True, path="b.py"),
    ]
    return row


def test_pull_request_identity_rejects_untrusted_path_components() -> None:
    assert pull_request_status.pull_request_identity(
        {"repo_full_name": "owner/repo", "number": 7}
    ) == ("owner", "repo", 7)
    assert (
        pull_request_status.pull_request_identity(
            {"repo_full_name": "owner/repo/../../users", "number": 7}
        )
        is None
    )
    assert (
        pull_request_status.pull_request_identity({"repo_full_name": "owner/repo", "number": True})
        is None
    )
    assert (
        pull_request_status.pull_request_identity({"repo_full_name": "owner/..", "number": 7})
        is None
    )


def test_merge_conflict_state_only_marks_confirmed_clean_as_mergeable() -> None:
    assert (
        pull_request_status._merge_conflict_state({"mergeable": True, "mergeable_state": "clean"})
        == "mergeable"
    )
    assert (
        pull_request_status._merge_conflict_state({"mergeable": True, "mergeable_state": "blocked"})
        == "unknown"
    )
    assert (
        pull_request_status._merge_conflict_state({"mergeable": False, "mergeable_state": "dirty"})
        == "conflicting"
    )


def test_normalize_checks_classifies_failures_and_pending() -> None:
    failing, pending, inconclusive = pull_request_status._normalize_checks(
        [
            {"name": "unit", "status": "completed", "conclusion": "failure", "details_url": "u"},
            {"name": "deploy", "status": "in_progress", "conclusion": None},
            {"name": "lint", "status": "completed", "conclusion": "skipped"},
        ],
        [
            {"context": "legacy", "state": "error", "target_url": "s"},
            {"context": "waiting", "state": "pending"},
        ],
    )

    assert pending == 2
    assert inconclusive == 1
    assert failing == [
        {"name": "unit", "conclusion": "failure", "url": "u"},
        {"name": "legacy", "conclusion": "error", "url": "s"},
    ]


async def test_get_statuses_normalizes_live_state_and_paginates_review_threads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(pull_request_status, "github_client", _client)
    graphql_pages: list[str | None] = []

    async def request(client, method, url, **kwargs):
        assert client is not None
        if url.endswith("/pulls/7"):
            return _response(
                200,
                {
                    "state": "closed",
                    "draft": False,
                    "merged": True,
                    "mergeable": False,
                    "mergeable_state": "dirty",
                    "head": {"sha": "a" * 40},
                },
            )
        if url == pull_request_status.GITHUB_GRAPHQL:
            cursor = kwargs["json"]["variables"]["cursor"]
            graphql_pages.append(cursor)
            if cursor is None:
                return _response(
                    200,
                    {
                        "data": {
                            "repository": {
                                "pullRequest": {
                                    "reviewThreads": {
                                        "nodes": [
                                            {
                                                "isResolved": False,
                                                "path": "a.py",
                                                "line": 4,
                                                "originalLine": None,
                                                "comments": {
                                                    "nodes": [
                                                        {
                                                            "author": {"login": "alice"},
                                                            "body": "fix this",
                                                            "url": "https://github.com/o/r/pull/7#discussion_r1",
                                                        }
                                                    ]
                                                },
                                            },
                                            {"isResolved": True},
                                        ],
                                        "pageInfo": {"hasNextPage": True, "endCursor": "next"},
                                    }
                                }
                            }
                        }
                    },
                )
            return _response(
                200,
                {
                    "data": {
                        "repository": {
                            "pullRequest": {
                                "reviewThreads": {
                                    "nodes": [
                                        {
                                            "isResolved": False,
                                            "path": "b.py",
                                            "line": None,
                                            "originalLine": 9,
                                            "comments": {
                                                "nodes": [
                                                    {
                                                        "author": {"login": "bob"},
                                                        "body": "question",
                                                        "url": "https://github.com/o/r/pull/7#discussion_r2",
                                                    }
                                                ]
                                            },
                                        }
                                    ],
                                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                                }
                            }
                        }
                    }
                },
            )
        if url.endswith("/check-runs"):
            return _response(
                200,
                {
                    "check_runs": [
                        {
                            "name": "unit",
                            "status": "completed",
                            "conclusion": "timed_out",
                            "details_url": "https://checks/unit",
                        },
                        {"name": "deploy", "status": "queued", "conclusion": None},
                    ]
                },
            )
        if url.endswith("/status"):
            return _response(
                200,
                {
                    "statuses": [
                        {
                            "context": "legacy",
                            "state": "failure",
                            "target_url": "https://checks/legacy",
                        }
                    ]
                },
            )
        raise AssertionError(url)

    monkeypatch.setattr(pull_request_status, "github_request", request)

    result = await pull_request_status.get_pull_request_statuses(
        [{"repo_full_name": "o/r", "number": 7}], "oauth-token"
    )

    assert graphql_pages == [None, "next"]
    assert result == [
        {
            "repoFullName": "o/r",
            "number": 7,
            "url": "https://github.com/o/r/pull/7",
            "statusAvailable": True,
            "state": "merged",
            "isDraft": False,
            "mergeConflictState": "conflicting",
            "checksAvailable": True,
            "failingChecks": [
                {"name": "unit", "conclusion": "timed_out", "url": "https://checks/unit"},
                {
                    "name": "legacy",
                    "conclusion": "failure",
                    "url": "https://checks/legacy",
                },
            ],
            "pendingCheckCount": 1,
            "inconclusiveCheckCount": 0,
            "commentsAvailable": True,
            "unresolvedReviewThreadCount": 2,
            "unresolvedReviewThreads": [
                {
                    "author": "alice",
                    "body": "fix this",
                    "path": "a.py",
                    "line": 4,
                    "url": "https://github.com/o/r/pull/7#discussion_r1",
                },
                {
                    "author": "bob",
                    "body": "question",
                    "path": "b.py",
                    "line": 9,
                    "url": "https://github.com/o/r/pull/7#discussion_r2",
                },
            ],
        }
    ]


async def test_one_inaccessible_pull_request_does_not_fail_the_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(pull_request_status, "github_client", _client)

    async def request(client, method, url, **kwargs):
        if url == pull_request_status.GITHUB_GRAPHQL:
            return _response(200, {"errors": [{"message": "not found"}]})
        return _response(404, {"message": "Not Found"})

    monkeypatch.setattr(pull_request_status, "github_request", request)

    result = await pull_request_status.get_pull_request_statuses(
        [
            {"repo_full_name": "private/repo", "number": 1},
            {"repo_full_name": "bad/repo/segment", "number": 2},
        ],
        "oauth-token",
    )

    assert len(result) == 2
    assert result[0]["statusAvailable"] is False
    assert result[0]["checksAvailable"] is False
    assert result[0]["commentsAvailable"] is False
    assert result[0]["pendingCheckCount"] is None
    assert result[0]["inconclusiveCheckCount"] is None
    assert result[1]["url"] is None


async def test_partial_check_failure_cannot_appear_green(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(pull_request_status, "github_client", _client)

    async def request(client, method, url, **kwargs):
        if url.endswith("/pulls/3"):
            return _response(
                200,
                {
                    "state": "open",
                    "draft": True,
                    "mergeable": None,
                    "mergeable_state": "unknown",
                    "head": {"sha": "b" * 40},
                },
            )
        if url == pull_request_status.GITHUB_GRAPHQL:
            return _response(
                200,
                {
                    "data": {
                        "repository": {
                            "pullRequest": {
                                "reviewThreads": {
                                    "nodes": [],
                                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                                }
                            }
                        }
                    }
                },
            )
        if url.endswith("/check-runs"):
            return _response(403, {"message": "checks permission missing"})
        if url.endswith("/status"):
            return _response(200, {"statuses": []})
        raise AssertionError(url)

    monkeypatch.setattr(pull_request_status, "github_request", request)

    result = (
        await pull_request_status.get_pull_request_statuses(
            [{"repo_full_name": "o/r", "number": 3}], "oauth-token"
        )
    )[0]

    assert result["statusAvailable"] is True
    assert result["state"] == "open"
    assert result["isDraft"] is True
    assert result["mergeConflictState"] == "unknown"
    assert result["checksAvailable"] is False
    assert result["failingChecks"] == []
    assert result["pendingCheckCount"] is None
    assert result["inconclusiveCheckCount"] is None
    assert result["commentsAvailable"] is True


async def test_fresh_stored_row_is_served_without_touching_github(
    monkeypatch: pytest.MonkeyPatch,
    stored_rows: list[PullRequest],
    write_backs: list[tuple[str, str, int, str]],
) -> None:
    async def request(*args, **kwargs):
        raise AssertionError("a fresh stored row must not reach GitHub")

    monkeypatch.setattr(pull_request_status, "github_client", _client)
    monkeypatch.setattr(pull_request_status, "github_request", request)
    stored_rows.append(_stored_row())

    result = await pull_request_status.get_pull_request_statuses(
        [{"repo_full_name": "o/r", "number": 7}], "oauth-token"
    )

    assert result == [
        {
            "repoFullName": "o/r",
            "number": 7,
            "url": "https://github.com/o/r/pull/7",
            "statusAvailable": True,
            "state": "open",
            "isDraft": True,
            "mergeConflictState": "conflicting",
            "checksAvailable": True,
            "failingChecks": [
                {"name": "unit", "conclusion": "timed_out", "url": "https://checks/unit"}
            ],
            "pendingCheckCount": 1,
            "inconclusiveCheckCount": 1,
            "commentsAvailable": True,
            "unresolvedReviewThreadCount": 1,
            "unresolvedReviewThreads": [
                {
                    "author": "alice",
                    "body": "fix this",
                    "path": "a.py",
                    "line": 4,
                    "url": "https://github.com/o/r/pull/7#discussion_r1",
                }
            ],
        }
    ]
    assert write_backs == []


async def test_stale_stored_row_falls_back_to_github_and_schedules_a_write_back(
    monkeypatch: pytest.MonkeyPatch,
    stored_rows: list[PullRequest],
    write_backs: list[tuple[str, str, int, str]],
) -> None:
    monkeypatch.setattr(pull_request_status, "github_client", _client)

    async def request(client, method, url, **kwargs):
        if url.endswith("/pulls/7"):
            return _response(
                200,
                {
                    "state": "open",
                    "draft": False,
                    "mergeable": True,
                    "mergeable_state": "clean",
                    "head": {"sha": "a" * 40},
                },
            )
        if url == pull_request_status.GITHUB_GRAPHQL:
            return _response(200, {"errors": [{"message": "nope"}]})
        return _response(200, {"check_runs": [], "statuses": []})

    monkeypatch.setattr(pull_request_status, "github_request", request)
    stored_rows.append(_stored_row(age=timedelta(hours=2)))

    result = await pull_request_status.get_pull_request_statuses(
        [{"repo_full_name": "o/r", "number": 7}], "oauth-token"
    )

    assert result[0]["state"] == "open"
    assert result[0]["isDraft"] is False
    assert result[0]["mergeConflictState"] == "mergeable"
    assert write_backs == [("o", "r", 7, "oauth-token")]


async def test_missing_stored_row_schedules_a_write_back(
    monkeypatch: pytest.MonkeyPatch, write_backs: list[tuple[str, str, int, str]]
) -> None:
    monkeypatch.setattr(pull_request_status, "github_client", _client)

    async def request(client, method, url, **kwargs):
        return _response(404, {"message": "Not Found"})

    monkeypatch.setattr(pull_request_status, "github_request", request)

    result = await pull_request_status.get_pull_request_statuses(
        [
            {"repo_full_name": "o/r", "number": 7},
            {"repo_full_name": "bad/repo/segment", "number": 2},
        ],
        "oauth-token",
    )

    assert result[0]["statusAvailable"] is False
    assert result[1]["url"] is None
    # An unparseable record never reaches GitHub, so it is never written back.
    assert write_backs == [("o", "r", 7, "oauth-token")]


async def test_thread_status_authorizes_read_access_before_token_or_metadata_use(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    order: list[str] = []
    records = [
        {"repo_full_name": "o/one", "number": 1},
        {"repo_full_name": "o/two", "number": 2},
    ]

    async def readable(thread_id: str, *, login: str | None = None, email: str | None = None):
        order.append("readable")
        assert (thread_id, login, email) == ("thread-1", "teammate", "teammate@example.com")
        return {"pull_requests": records}

    async def token(login: str):
        order.append("token")
        assert login == "teammate"
        return "oauth-token"

    statuses = AsyncMock(return_value=[{"number": 1}, {"number": 2}])
    patch_thread_module(monkeypatch, "_readable_thread_metadata", readable)
    patch_thread_module(monkeypatch, "_github_token_for_login", token)
    patch_thread_module(monkeypatch, "get_pull_request_statuses", statuses)

    result = await handlers.get_dashboard_thread_pull_request_status(
        "thread-1", "teammate", email="teammate@example.com"
    )

    assert order == ["readable", "token"]
    statuses.assert_awaited_once_with(records, "oauth-token")
    assert result == {"pullRequests": [{"number": 1}, {"number": 2}]}


async def test_thread_status_requires_the_users_oauth_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = AsyncMock(return_value=None)
    patch_thread_module(monkeypatch, "get_valid_access_token", token)

    with pytest.raises(HTTPException) as exc_info:
        await thread_access._github_token_for_login("owner")

    assert exc_info.value.status_code == 401
    token.assert_awaited_once_with("owner")


async def test_thread_status_read_denial_does_not_resolve_oauth_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def denied(*args, **kwargs):
        raise HTTPException(403, "thread is not readable")

    token = AsyncMock(return_value="oauth-token")
    patch_thread_module(monkeypatch, "_readable_thread_metadata", denied)
    patch_thread_module(monkeypatch, "_github_token_for_login", token)

    with pytest.raises(HTTPException) as exc_info:
        await handlers.get_dashboard_thread_pull_request_status("thread-1", "intruder")

    assert exc_info.value.status_code == 403
    token.assert_not_awaited()
