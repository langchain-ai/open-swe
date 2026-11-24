import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import httpx2
import pytest
from fastapi import HTTPException

from openswe.dashboard import profiles
from openswe.github import http as github_http
from openswe.github import pull_request_status
from openswe.github.pull_request_key import PullRequestKey
from openswe.threads import handlers
from openswe.ui_invalidations.topics import Topic
from tests.conftest import patch_thread_module
from tests.dashboard.test_my_pull_requests import serve_status, status_answer


def _response(status: int, payload: object) -> httpx2.Response:
    return httpx2.Response(
        status, json=payload, request=httpx2.Request("GET", "https://api.github.com")
    )


def _github(authorization: str | None = "Bearer test-token") -> github_http.GitHubClient:
    headers = httpx2.Headers({"Authorization": authorization} if authorization else {})
    return github_http.GitHubClient(MagicMock(headers=headers))


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
    Mergeability = pull_request_status.Mergeability
    assert Mergeability(True, "clean").conflict_state == "mergeable"
    assert Mergeability(True, "blocked").conflict_state == "unknown"
    assert Mergeability(False, "dirty").conflict_state == "conflicting"


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
    graphql_pages: list[str | None] = []
    first_threads = {
        "nodes": [
            {
                "id": "PRRT_1",
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
    later_threads = {
        "nodes": [
            {
                "id": "PRRT_2",
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
                        },
                        {
                            "author": {"login": "alice"},
                            "body": "answer",
                            "url": "https://github.com/o/r/pull/7#discussion_r3",
                        },
                    ]
                },
            }
        ],
        "pageInfo": {"hasNextPage": False, "endCursor": None},
    }

    runs = [
        {
            "id": 1,
            "name": "unit",
            "status": "completed",
            "conclusion": "timed_out",
            "details_url": "https://checks/unit",
        },
        {"id": 2, "name": "deploy", "status": "queued", "conclusion": None},
    ]
    statuses = [{"context": "legacy", "state": "failure", "target_url": "https://checks/legacy"}]

    async def request(client, method, url, **kwargs):
        assert client is not None
        # The thread's checks come over REST alone; the query never asks GitHub for them.
        if url.endswith("/pulls/7"):
            return _response(200, {"head": {"sha": "a" * 40}})
        if url.endswith("/check-runs"):
            return _response(200, {"total_count": len(runs), "check_runs": runs})
        if url.endswith("/status"):
            return _response(200, {"total_count": len(statuses), "statuses": statuses})
        assert url == github_http.GITHUB_GRAPHQL
        variables = kwargs["json"]["variables"]
        if "cursor" not in variables:
            assert variables["threadComments"] is True
            assert variables["ci"] is False
            return _response(
                200,
                status_answer(
                    state="MERGED",
                    mergeable="CONFLICTING",
                    merge_state="DIRTY",
                    reviewThreads=first_threads,
                ),
            )
        graphql_pages.append(variables["cursor"])
        return _response(
            200, {"data": {"repository": {"pullRequest": {"reviewThreads": later_threads}}}}
        )

    monkeypatch.setattr(github_http, "github_request", request)

    result = await pull_request_status.get_pull_request_statuses(
        _github(), [{"repo_full_name": "o/r", "number": 7}]
    )

    assert graphql_pages == ["next"]
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
                    "thread_id": "PRRT_1",
                    "author": "alice",
                    "body": "fix this",
                    "path": "a.py",
                    "line": 4,
                    "url": "https://github.com/o/r/pull/7#discussion_r1",
                    "replies": [],
                },
                {
                    "thread_id": "PRRT_2",
                    "author": "bob",
                    "body": "question",
                    "path": "b.py",
                    "line": 9,
                    "url": "https://github.com/o/r/pull/7#discussion_r2",
                    "replies": [
                        {
                            "author": "alice",
                            "body": "answer",
                            "url": "https://github.com/o/r/pull/7#discussion_r3",
                        }
                    ],
                },
            ],
        }
    ]


async def test_one_inaccessible_pull_request_does_not_fail_the_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    async def request(client, method, url, **kwargs):
        if url == github_http.GITHUB_GRAPHQL:
            return _response(200, {"errors": [{"message": "not found"}]})
        return _response(404, {"message": "Not Found"})

    monkeypatch.setattr(github_http, "github_request", request)

    result = await pull_request_status.get_pull_request_statuses(
        _github(),
        [
            {"repo_full_name": "private/repo", "number": 1},
            {"repo_full_name": "bad/repo/segment", "number": 2},
        ],
    )

    assert len(result) == 2
    assert result[0]["statusAvailable"] is False
    assert result[0]["checksAvailable"] is False
    assert result[0]["commentsAvailable"] is False
    assert result[0]["pendingCheckCount"] is None
    assert result[0]["inconclusiveCheckCount"] is None
    assert result[1]["url"] is None


async def test_checks_read_before_a_push_are_read_again_for_the_new_head(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    old, new = "b" * 40, "a" * 40
    failing = {"id": 1, "name": "unit", "status": "completed", "conclusion": "failure"}
    checked: list[str] = []

    async def check_runs(_repo, sha: str, **_kwargs):
        checked.append(sha)
        return [failing] if sha == new else []

    monkeypatch.setattr(github_http.RepoClient, "check_runs", check_runs)
    monkeypatch.setattr(github_http.RepoClient, "commit_statuses", AsyncMock(return_value=[]))
    # REST still names the commit before the push; the status query already sees the new one.
    monkeypatch.setattr(
        pull_request_status.PullRequestClient, "head_sha", AsyncMock(return_value=old)
    )
    serve_status(monkeypatch, status_answer(sha=new))

    [result] = await pull_request_status.get_pull_request_statuses(
        _github(), [{"repo_full_name": "o/r", "number": 3}]
    )

    assert checked == [old, new]
    assert [check["name"] for check in result["failingChecks"]] == ["unit"]


@pytest.fixture
def shared_cache(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    """The platform's cross-replica cache, as a dict."""
    store: dict[str, object] = {}

    async def get(key: str) -> object:
        return store.get(key)

    async def put(key: str, value: object, ttl: timedelta | None = None) -> None:
        store[key] = value

    monkeypatch.setattr("langgraph_api.cache.cache_get", get)
    monkeypatch.setattr("langgraph_api.cache.cache_set", put)
    return store


@pytest.mark.usefixtures("registry_db", "shared_cache")
async def test_the_head_is_asked_of_github_again_only_after_the_pull_request_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sha = "a" * 40
    checked: list[str] = []

    async def check_runs(_repo, head: str, **_kwargs):
        checked.append(head)
        return []

    monkeypatch.setattr(github_http.RepoClient, "check_runs", check_runs)
    monkeypatch.setattr(github_http.RepoClient, "commit_statuses", AsyncMock(return_value=[]))
    head_sha = AsyncMock(return_value=sha)
    monkeypatch.setattr(pull_request_status.PullRequestClient, "head_sha", head_sha)
    serve_status(monkeypatch, status_answer(sha=sha))
    records = [{"repo_full_name": "o/head-kept", "number": 3}]

    async def poll() -> None:
        [result] = await pull_request_status.get_pull_request_statuses(_github(), records)
        assert result["checksAvailable"] is True

    await poll()
    await poll()
    assert head_sha.await_count == 1
    assert checked == [sha, sha]

    # A push is reported as a change, after which the kept head may be the old one.
    await Topic.invalidate_pull_request(
        PullRequestKey.of("o", "head-kept", 3), *Topic.for_pull_request_event("pull_request")
    )
    await poll()
    assert head_sha.await_count == 2


@pytest.mark.usefixtures("shared_cache")
@pytest.mark.parametrize("other_authorization", ["Bearer denied-token", None])
async def test_cached_head_is_not_used_by_another_credential(
    monkeypatch: pytest.MonkeyPatch, other_authorization: str | None
) -> None:
    sha = "a" * 40
    checked: list[str] = []

    async def check_runs(_repo, head: str, **_kwargs):
        checked.append(head)
        return []

    monkeypatch.setattr(Topic, "last_changed", AsyncMock(return_value=(None, datetime.now(UTC))))
    monkeypatch.setattr(github_http.RepoClient, "check_runs", check_runs)
    monkeypatch.setattr(github_http.RepoClient, "commit_statuses", AsyncMock(return_value=[]))
    head_sha = AsyncMock(return_value=sha)
    monkeypatch.setattr(pull_request_status.PullRequestClient, "head_sha", head_sha)
    serve_status(monkeypatch, status_answer(sha=sha))
    records = [{"repo_full_name": "o/head-kept", "number": 3}]

    for _ in range(2):
        [allowed] = await pull_request_status.get_pull_request_statuses(_github(), records)
        assert allowed["checksAvailable"] is True
    assert head_sha.await_count == 1

    head_sha.side_effect = ValueError("Not Found")
    serve_status(monkeypatch, {"errors": [{"message": "not found"}]})
    [denied] = await pull_request_status.get_pull_request_statuses(
        _github(other_authorization), records
    )

    assert denied["statusAvailable"] is False
    assert denied["checksAvailable"] is False
    assert checked == [sha, sha]
    assert head_sha.await_count == 2


async def test_a_threads_pull_requests_are_read_a_few_at_once_in_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    running = 0
    most = 0

    async def thread_status(client: pull_request_status.PullRequestClient) -> dict[str, object]:
        nonlocal running, most
        running += 1
        most = max(most, running)
        await asyncio.sleep(0.01)
        running -= 1
        return {"number": client.number}

    monkeypatch.setattr(pull_request_status.PullRequestClient, "thread_status", thread_status)
    records = [{"repo_full_name": "o/r", "number": number} for number in range(1, 11)]

    result = await pull_request_status.get_pull_request_statuses(_github(), records)

    assert [status["number"] for status in result] == list(range(1, 11))
    assert most == pull_request_status._THREAD_STATUS_CONCURRENCY


@pytest.mark.parametrize("failed_read", ["check_runs", "commit_statuses"])
async def test_partial_check_failure_cannot_appear_green(
    monkeypatch: pytest.MonkeyPatch,
    failed_read: str,
) -> None:
    monkeypatch.setattr(github_http.RepoClient, "check_runs", AsyncMock(return_value=[]))
    monkeypatch.setattr(github_http.RepoClient, "commit_statuses", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        github_http.RepoClient, failed_read, AsyncMock(side_effect=ValueError("Unavailable"))
    )
    monkeypatch.setattr(
        pull_request_status.PullRequestClient, "head_sha", AsyncMock(return_value="a" * 40)
    )
    serve_status(
        monkeypatch,
        status_answer(
            isDraft=True, mergeable="UNKNOWN", merge_state="UNKNOWN", unreadable=["commits"]
        ),
    )

    result = (
        await pull_request_status.get_pull_request_statuses(
            _github(), [{"repo_full_name": "o/r", "number": 3}]
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
    patch_thread_module(monkeypatch, "_readable_thread_metadata", readable)
    monkeypatch.setattr(profiles, "get_valid_access_token", token)
    patch_thread_module(monkeypatch, "get_pull_request_statuses", statuses)

    result = await handlers.get_dashboard_thread_pull_request_status(
        "thread-1", "teammate", email="teammate@example.com"
    )

    assert order == ["readable", "token"]
    github, tracked = statuses.await_args.args
    assert github.http.headers["Authorization"] == "Bearer oauth-token"
    assert tracked == records
    assert result == {"pullRequests": [{"number": 1}, {"number": 2}]}


async def test_thread_status_read_denial_does_not_resolve_oauth_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def denied(*args, **kwargs):
        raise HTTPException(403, "thread is not readable")

    token = AsyncMock(return_value="oauth-token")
    patch_thread_module(monkeypatch, "_readable_thread_metadata", denied)
    monkeypatch.setattr(profiles, "get_valid_access_token", token)

    with pytest.raises(HTTPException) as exc_info:
        await handlers.get_dashboard_thread_pull_request_status("thread-1", "intruder")

    assert exc_info.value.status_code == 403
    token.assert_not_awaited()


async def test_finding_thread_states_read_every_page(monkeypatch: pytest.MonkeyPatch) -> None:
    async def graphql(self, query: str, variables: dict[str, object]) -> dict[str, object]:
        cursor = variables["cursor"]
        return {
            "repository": {
                "pullRequest": {
                    "reviewThreads": {
                        "nodes": [
                            {"id": "A" if cursor is None else "B", "isResolved": cursor is None}
                        ],
                        "pageInfo": {"hasNextPage": cursor is None, "endCursor": "next"},
                    }
                }
            }
        }

    monkeypatch.setattr(github_http.RepoClient, "graphql", graphql)
    pull = _github().repo("acme", "app").pull_request(7)
    assert await pull.review_thread_states() == {"A": True, "B": False}


@pytest.mark.parametrize(
    "last_page",
    [
        {"nodes": [], "pageInfo": {"hasNextPage": True, "endCursor": "next"}},
        {"nodes": []},
        {"nodes": [], "pageInfo": {"hasNextPage": "false"}},
        {"nodes": [{"id": "B"}], "pageInfo": {"hasNextPage": False}},
        ValueError("GitHub GraphQL error on page two"),
        httpx2.ConnectError("GitHub unavailable on page two"),
    ],
)
async def test_finding_thread_states_never_return_a_partial_snapshot(
    monkeypatch: pytest.MonkeyPatch, last_page: object
) -> None:
    async def graphql(self, query: str, variables: dict[str, object]) -> dict[str, object]:
        if variables["cursor"] is None:
            connection = {
                "nodes": [{"id": "A", "isResolved": True}],
                "pageInfo": {"hasNextPage": True, "endCursor": "next"},
            }
        elif isinstance(last_page, Exception):
            raise last_page
        else:
            connection = last_page
        return {"repository": {"pullRequest": {"reviewThreads": connection}}}

    monkeypatch.setattr(github_http.RepoClient, "graphql", graphql)
    pull = _github().repo("acme", "app").pull_request(7)
    with pytest.raises((ValueError, httpx2.HTTPError)):
        await pull.review_thread_states()
