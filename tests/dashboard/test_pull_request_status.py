from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import HTTPException

from agent.dashboard import thread_api
from agent.github import pull_request_status


def _response(status: int, payload: object) -> httpx.Response:
    return httpx.Response(
        status, json=payload, request=httpx.Request("GET", "https://api.github.com")
    )


@asynccontextmanager
async def _client(**kwargs):
    assert kwargs == {"token": "oauth-token"}
    yield object()


def test_pull_request_identity_rejects_untrusted_path_components() -> None:
    assert pull_request_status._pull_request_identity(
        {"repo_full_name": "owner/repo", "number": 7}
    ) == ("owner", "repo", 7)
    assert (
        pull_request_status._pull_request_identity(
            {"repo_full_name": "owner/repo/../../users", "number": 7}
        )
        is None
    )
    assert (
        pull_request_status._pull_request_identity({"repo_full_name": "owner/repo", "number": True})
        is None
    )
    assert (
        pull_request_status._pull_request_identity({"repo_full_name": "owner/..", "number": 7})
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
    monkeypatch.setattr(thread_api, "_readable_thread_metadata", readable)
    monkeypatch.setattr(thread_api, "_github_token_for_login", token)
    monkeypatch.setattr(thread_api, "get_pull_request_statuses", statuses)

    result = await thread_api.get_dashboard_thread_pull_request_status(
        "thread-1", "teammate", email="teammate@example.com"
    )

    assert order == ["readable", "token"]
    statuses.assert_awaited_once_with(records, "oauth-token")
    assert result == {"pullRequests": [{"number": 1}, {"number": 2}]}


async def test_thread_status_requires_the_users_oauth_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = AsyncMock(return_value=None)
    monkeypatch.setattr(thread_api, "get_valid_access_token", token)

    with pytest.raises(HTTPException) as exc_info:
        await thread_api._github_token_for_login("owner")

    assert exc_info.value.status_code == 401
    token.assert_awaited_once_with("owner")


async def test_thread_status_read_denial_does_not_resolve_oauth_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def denied(*args, **kwargs):
        raise HTTPException(403, "thread is not readable")

    token = AsyncMock(return_value="oauth-token")
    monkeypatch.setattr(thread_api, "_readable_thread_metadata", denied)
    monkeypatch.setattr(thread_api, "_github_token_for_login", token)

    with pytest.raises(HTTPException) as exc_info:
        await thread_api.get_dashboard_thread_pull_request_status("thread-1", "intruder")

    assert exc_info.value.status_code == 403
    token.assert_not_awaited()
