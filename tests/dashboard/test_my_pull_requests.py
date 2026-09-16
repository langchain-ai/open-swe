from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import httpx2
import pytest
from fastapi import HTTPException

from agent.github import pull_request_dashboard_routes as pr_routes
from agent.github import pull_request_status as prs
from agent.github import ready_pull_request as ready
from agent.review import routes as review_routes


@asynccontextmanager
async def client(**kwargs):
    assert kwargs == {"token": "user-token"}
    yield object()


def response(payload, status=200):
    return httpx2.Response(
        status, json=payload, request=httpx2.Request("GET", "https://api.github.com")
    )


async def test_lightweight_list_uses_server_sort_and_does_not_wait_for_details(monkeypatch):
    monkeypatch.setattr(prs, "github_client", client)
    search = AsyncMock(
        return_value=response(
            {
                "items": [
                    {
                        "number": 1,
                        "title": "PR 1",
                        "repository_url": "https://api.github.com/repos/acme/app",
                        "created_at": "2026-01-01T00:00:00Z",
                    }
                ],
                "total_count": 1,
            }
        )
    )
    monkeypatch.setattr(prs, "github_request", search)
    details = AsyncMock()
    monkeypatch.setattr(prs, "_fetch_pull_request", details)
    result = await prs.list_open_pull_requests(
        "octocat", "user-token", lightweight=True, sort="created", direction="asc"
    )
    details.assert_not_awaited()
    assert search.await_args.kwargs["params"]["sort"] == "created"
    assert search.await_args.kwargs["params"]["order"] == "asc"
    assert result.pull_requests[0].title == "PR 1"
    assert result.pull_requests[0].details_loading is True


async def test_payload_json_keys_stay_camel_case_for_the_dashboard_client():
    pull = await prs.load_open_pull_request(
        object(), {"repo_full_name": "acme/app", "number": 1}, details=False
    )
    assert pull is not None
    assert set(pull.model_dump(by_alias=True, mode="json")) == {
        "repo",
        "number",
        "title",
        "draft",
        "additions",
        "deletions",
        "mergeable",
        "mergeState",
        "headSha",
        "headRef",
        "reviewDecision",
        "unresolvedThreads",
        "statusAvailable",
        "createdAt",
        "updatedAt",
        "ci",
        "failingChecks",
        "pendingChecks",
        "detailsLoading",
    }
    payload = prs.OpenPullRequests(
        pull_requests=[pull], next_page=2, incomplete=False, updated_at="2026-01-01T00:00:00Z"
    )
    assert set(payload.model_dump(by_alias=True, mode="json")) == {
        "pullRequests",
        "nextPage",
        "incomplete",
        "updatedAt",
    }


async def test_open_prs_use_live_state_current_head_and_legacy_statuses(monkeypatch):
    monkeypatch.setattr(prs, "github_client", client)
    queries = []

    async def request(_client, method, url, **kwargs):
        if method == "POST":
            return _threads_response([False, True])
        assert method == "GET"
        if url.endswith("/search/issues"):
            queries.append(kwargs["params"]["q"])
            return response(
                {
                    "items": [
                        {
                            "number": n,
                            "title": f"PR {n}",
                            "repository_url": "https://api.github.com/repos/acme/app",
                        }
                        for n in range(1, 5)
                    ],
                    "total_count": 4,
                }
            )
        if url.endswith("/reviews"):
            return response([])
        if "/pulls/" in url:
            n = int(url.rsplit("/", 1)[1])
            if n == 3:
                return response({}, 403)
            return response(
                {
                    "state": "closed" if n == 2 else "open",
                    "merged": n == 2,
                    "title": f"Current {n}",
                    "draft": False,
                    "additions": n,
                    "deletions": 4,
                    "mergeable": True,
                    "mergeable_state": "blocked",
                    "head": {"sha": str(n) * 40},
                    "created_at": "2026-09-01T00:00:00Z",
                    "updated_at": "2026-09-12T00:00:00Z",
                }
            )
        assert "/commits/" in url
        sha = url.split("/commits/")[1].split("/")[0]
        assert sha in {"1" * 40, "4" * 40}
        if url.endswith("check-runs"):
            return response(
                {
                    "check_runs": [{"name": "unit", "status": "in_progress"}]
                    if sha.startswith("1")
                    else []
                }
            )
        return response(
            {
                "statuses": [{"context": "legacy-ci", "state": "failure"}]
                if sha.startswith("1")
                else []
            }
        )

    monkeypatch.setattr(prs, "github_request", request)
    result = await prs.list_open_pull_requests("octocat", "user-token", "acme/app")
    assert queries == ["is:pr is:open author:octocat repo:acme/app"]
    assert [pr.number for pr in result.pull_requests] == [1, 3, 4]
    live, unavailable, no_checks = result.pull_requests
    assert live.ci == "failing"
    assert live.failing_checks == ["legacy-ci"]
    assert live.pending_checks == ["unit"]
    assert live.mergeable is True and live.merge_state == "blocked"
    assert live.unresolved_threads == 1
    assert (live.additions, live.deletions) == (1, 4)
    assert live.updated_at == "2026-09-12T00:00:00Z"
    assert unavailable.status_available is False and unavailable.ci == "unknown"
    assert no_checks.ci == "none"


@pytest.mark.parametrize("repo", ["acme/app is:closed", "acme/../secrets", "acme/.."])
async def test_repository_filter_cannot_change_query_or_path(repo):
    with pytest.raises(HTTPException) as error:
        await prs.list_open_pull_requests("octocat", "user-token", repo)
    assert error.value.status_code == 422


async def test_search_failure_is_not_an_empty_success(monkeypatch):
    monkeypatch.setattr(prs, "github_client", client)
    monkeypatch.setattr(prs, "github_request", AsyncMock(return_value=response({}, 429)))
    with pytest.raises(HTTPException) as error:
        await prs.list_open_pull_requests("octocat", "user-token")
    assert error.value.status_code == 502


async def test_search_pages_through_results(monkeypatch):
    monkeypatch.setattr(prs, "github_client", client)
    search = AsyncMock(return_value=response({"items": [], "total_count": 250}))
    monkeypatch.setattr(prs, "github_request", search)
    result = await prs.list_open_pull_requests("octocat", "user-token")
    assert search.await_args.kwargs["params"]["page"] == "1"
    assert result.next_page == 2 and result.incomplete is False
    result = await prs.list_open_pull_requests("octocat", "user-token", page=3)
    assert search.await_args.kwargs["params"]["page"] == "3"
    assert result.next_page is None
    search.return_value = response({"items": [], "total_count": 5000})
    result = await prs.list_open_pull_requests("octocat", "user-token", page=10)
    assert result.next_page is None
    with pytest.raises(HTTPException) as error:
        await prs.list_open_pull_requests("octocat", "user-token", page=11)
    assert error.value.status_code == 422


async def test_a_timed_out_search_returns_no_pull_requests(monkeypatch):
    """GitHub answers a timed-out search with an arbitrary subset of the matches."""
    monkeypatch.setattr(prs, "github_client", client)
    monkeypatch.setattr(
        prs,
        "github_request",
        AsyncMock(
            return_value=response(
                {
                    "items": [
                        {
                            "number": 1,
                            "title": "PR 1",
                            "repository_url": "https://api.github.com/repos/acme/app",
                        }
                    ],
                    "total_count": 86,
                    "incomplete_results": True,
                }
            )
        ),
    )
    result = await prs.list_open_pull_requests("octocat", "user-token", lightweight=True)
    assert result.pull_requests == []
    assert result.incomplete is True and result.next_page is None


async def test_pending_mergeability_is_awaited_rather_than_reported_unknown(monkeypatch):
    """GitHub answers `mergeable: null` until it finishes computing the merge."""
    monkeypatch.setattr(prs, "_MERGEABILITY_DELAY_SECONDS", 0)
    open_pull = {"state": "open", "head": {"sha": "a" * 40}}
    fetches = AsyncMock(
        side_effect=[
            {**open_pull, "mergeable": None, "mergeable_state": "unknown"},
            {**open_pull, "mergeable": None, "mergeable_state": "unknown"},
            {**open_pull, "mergeable": True, "mergeable_state": "clean"},
        ]
    )
    monkeypatch.setattr(prs, "_fetch_pull_request", fetches)
    monkeypatch.setattr(prs, "_fetch_check_runs", AsyncMock(return_value=[]))
    monkeypatch.setattr(prs, "_fetch_commit_statuses", AsyncMock(return_value=[]))
    monkeypatch.setattr(prs, "_fetch_review_decision", AsyncMock(return_value="approved"))
    monkeypatch.setattr(prs, "_fetch_unresolved_thread_count", AsyncMock(return_value=0))
    result = await prs.load_open_pull_request(object(), {"repo_full_name": "acme/app", "number": 1})
    assert fetches.await_count == 3
    assert result is not None
    assert result.mergeable is True and result.merge_state == "clean"


async def test_mergeability_is_not_awaited_forever(monkeypatch):
    monkeypatch.setattr(prs, "_MERGEABILITY_DELAY_SECONDS", 0)
    fetches = AsyncMock(
        return_value={"state": "open", "mergeable": None, "mergeable_state": "unknown"}
    )
    monkeypatch.setattr(prs, "_fetch_pull_request", fetches)
    monkeypatch.setattr(prs, "_fetch_review_decision", AsyncMock(return_value="none"))
    result = await prs.load_open_pull_request(object(), {"repo_full_name": "acme/app", "number": 1})
    assert fetches.await_count == prs._MERGEABILITY_ATTEMPTS
    assert result is not None
    assert result.status_available is True and result.mergeable is None


async def test_route_uses_signed_in_user_token_and_rejects_missing_auth(monkeypatch):
    token = AsyncMock(return_value="user-token")
    listing = AsyncMock(
        return_value=prs.OpenPullRequests(
            pull_requests=[], next_page=None, incomplete=False, updated_at="2026-01-01T00:00:00Z"
        )
    )
    monkeypatch.setattr(pr_routes, "get_valid_access_token", token)
    monkeypatch.setattr(pr_routes, "list_open_pull_requests", listing)
    await pr_routes.api_list_my_pull_requests(repo="acme/app", session={"sub": "octocat"})
    listing.assert_awaited_once_with(
        "octocat",
        "user-token",
        "acme/app",
        lightweight=False,
        sort="updated",
        direction="desc",
        page=1,
    )
    token.return_value = None
    with pytest.raises(HTTPException) as error:
        await pr_routes.api_list_my_pull_requests(session={"sub": "another-user"})
    assert error.value.status_code == 401
    assert listing.await_count == 1


async def test_review_decision_uses_latest_active_decision_per_reviewer(monkeypatch):
    monkeypatch.setattr(
        prs,
        "github_request",
        AsyncMock(
            return_value=response(
                [
                    {"id": 1, "user": {"login": "reviewer"}, "state": "CHANGES_REQUESTED"},
                    {"id": 2, "user": {"login": "reviewer"}, "state": "APPROVED"},
                    {"id": 3, "user": {"login": "reviewer"}, "state": "COMMENTED"},
                    {"id": 4, "user": {"login": "other"}, "state": "DISMISSED"},
                ]
            )
        ),
    )
    assert await prs._fetch_review_decision(object(), "acme", "app", 1) == "approved"
    monkeypatch.setattr(
        prs,
        "github_request",
        AsyncMock(
            return_value=response(
                [
                    {"id": 1, "user": {"login": "reviewer"}, "state": "APPROVED"},
                    {"id": 2, "user": {"login": "reviewer"}, "state": "DISMISSED"},
                ]
            )
        ),
    )
    assert await prs._fetch_review_decision(object(), "acme", "app", 1) == "none"
    monkeypatch.setattr(
        prs,
        "github_request",
        AsyncMock(
            return_value=response(
                [
                    {"id": 1, "user": {"login": "reviewer"}, "state": "APPROVED"},
                    {"id": 2, "user": {"login": "other"}, "state": "CHANGES_REQUESTED"},
                ]
            )
        ),
    )
    assert await prs._fetch_review_decision(object(), "acme", "app", 1) == "changes_requested"


async def test_review_indicators_require_repo_access_before_reading(monkeypatch):
    lookup = AsyncMock(return_value={})
    monkeypatch.setattr(review_routes, "get_review_summaries", lookup)
    monkeypatch.setattr(
        review_routes,
        "accessible_repo_full_names",
        AsyncMock(return_value=frozenset({"acme/allowed"})),
    )
    payload = review_routes.ReviewSummariesRequest(
        pull_requests=[{"repo": "acme/private", "number": 1}]
    )
    assert await review_routes.api_get_review_summaries(payload, session={"sub": "octocat"}) == {}
    lookup.assert_not_awaited()
    payload = review_routes.ReviewSummariesRequest(
        pull_requests=[
            {"repo": "acme/allowed", "number": 1},
            {"repo": "acme/private", "number": 2},
        ]
    )
    await review_routes.api_get_review_summaries(payload, session={"sub": "octocat"})
    lookup.assert_awaited_once_with([("acme", "allowed", 1)])


def _node_lookup(node_id, is_draft):
    return response({"data": {"repository": {"pullRequest": {"id": node_id, "isDraft": is_draft}}}})


async def test_marking_ready_resolves_the_node_id_then_confirms_the_mutation(monkeypatch):
    monkeypatch.setattr(ready, "github_client", client)
    monkeypatch.setattr(ready, "GITHUB_GRAPHQL", "https://fake-gh/graphql")
    request = AsyncMock(
        side_effect=[
            _node_lookup("PR_node_7", True),
            response(
                {"data": {"markPullRequestReadyForReview": {"pullRequest": {"isDraft": False}}}}
            ),
        ]
    )
    monkeypatch.setattr(ready, "github_request", request)
    result = await ready.mark_pull_request_ready("acme", "app", 7, "user-token")
    assert result == ready.ReadyPullRequestResult(ready=True)
    assert [call.args[1:] for call in request.await_args_list] == [
        ("POST", "https://fake-gh/graphql"),
        ("POST", "https://fake-gh/graphql"),
    ]
    assert request.await_args_list[0].kwargs["json"]["variables"] == {
        "owner": "acme",
        "repo": "app",
        "number": 7,
    }
    assert request.await_args_list[1].kwargs["json"]["variables"] == {"pullRequestId": "PR_node_7"}


async def test_a_pull_request_already_out_of_draft_is_ready_without_a_mutation(monkeypatch):
    monkeypatch.setattr(ready, "github_client", client)
    request = AsyncMock(side_effect=[_node_lookup("PR_node_7", False)])
    monkeypatch.setattr(ready, "github_request", request)
    assert await ready.mark_pull_request_ready("acme", "app", 7, "user-token") == (
        ready.ReadyPullRequestResult(ready=True)
    )
    assert request.await_count == 1


async def test_a_refused_mutation_surfaces_githubs_own_message(monkeypatch):
    monkeypatch.setattr(ready, "github_client", client)
    monkeypatch.setattr(
        ready,
        "github_request",
        AsyncMock(
            side_effect=[
                _node_lookup("PR_node_7", True),
                response({"errors": [{"message": "Resource not accessible by integration"}]}),
            ]
        ),
    )
    with pytest.raises(HTTPException, match="Resource not accessible by integration"):
        await ready.mark_pull_request_ready("acme", "app", 7, "user-token")


async def test_marking_ready_rejects_an_invalid_pull_request():
    with pytest.raises(HTTPException) as error:
        await ready.mark_pull_request_ready("acme", "app", 0, "user-token")
    assert error.value.status_code == 422


async def test_ready_route_forwards_the_pull_request_and_requires_a_token(monkeypatch):
    token = AsyncMock(return_value="user-token")
    mark = AsyncMock(return_value=ready.ReadyPullRequestResult(ready=True))
    monkeypatch.setattr(pr_routes, "get_valid_access_token", token)
    monkeypatch.setattr(pr_routes, "mark_pull_request_ready", mark)
    assert await pr_routes.api_ready_my_pull_request("acme", "app", 7, {"sub": "octocat"}) == (
        ready.ReadyPullRequestResult(ready=True)
    )
    mark.assert_awaited_once_with("acme", "app", 7, "user-token")
    token.return_value = None
    with pytest.raises(HTTPException) as error:
        await pr_routes.api_ready_my_pull_request("acme", "app", 7, {"sub": "octocat"})
    assert error.value.status_code == 401
    assert mark.await_count == 1


def _patch_detail_fetchers(monkeypatch):
    monkeypatch.setattr(
        prs,
        "_fetch_pull_request",
        AsyncMock(
            return_value={
                "state": "open",
                "draft": False,
                "mergeable": True,
                "mergeable_state": "clean",
                "head": {"ref": "feature", "sha": "a" * 40},
            }
        ),
    )
    monkeypatch.setattr(prs, "_fetch_check_runs", AsyncMock(return_value=[]))
    monkeypatch.setattr(prs, "_fetch_commit_statuses", AsyncMock(return_value=[]))
    monkeypatch.setattr(prs, "_fetch_review_decision", AsyncMock(return_value="approved"))
    monkeypatch.setattr(prs, "GITHUB_GRAPHQL", "https://fake-gh/graphql")


def _threads_response(resolved_flags, *, has_next=False, cursor=None):
    return response(
        {
            "data": {
                "repository": {
                    "pullRequest": {
                        "reviewThreads": {
                            "nodes": [{"isResolved": flag} for flag in resolved_flags],
                            "pageInfo": {"hasNextPage": has_next, "endCursor": cursor},
                        }
                    }
                }
            }
        }
    )


async def test_unresolved_threads_are_counted_from_the_modules_graphql_endpoint(monkeypatch):
    _patch_detail_fetchers(monkeypatch)
    graphql = AsyncMock(
        side_effect=[
            _threads_response([False, True, False], has_next=True, cursor="page-2"),
            _threads_response([True, False]),
        ]
    )
    monkeypatch.setattr(prs, "github_request", graphql)
    result = await prs.load_open_pull_request(object(), {"repo_full_name": "acme/app", "number": 7})
    assert result is not None
    assert result.unresolved_threads == 3
    assert [call.args[1:] for call in graphql.await_args_list] == [
        ("POST", "https://fake-gh/graphql"),
        ("POST", "https://fake-gh/graphql"),
    ]
    assert [call.kwargs["json"]["variables"] for call in graphql.await_args_list] == [
        {"owner": "acme", "repo": "app", "number": 7, "cursor": None},
        {"owner": "acme", "repo": "app", "number": 7, "cursor": "page-2"},
    ]


async def test_a_graphql_failure_leaves_the_unresolved_count_unknown(monkeypatch):
    _patch_detail_fetchers(monkeypatch)
    monkeypatch.setattr(
        prs,
        "github_request",
        AsyncMock(return_value=response({"errors": [{"message": "Bad credentials"}]})),
    )
    result = await prs.load_open_pull_request(object(), {"repo_full_name": "acme/app", "number": 7})
    assert result is not None
    assert result.unresolved_threads is None
    assert result.review_decision == "approved"
    assert result.head_sha == "a" * 40
    assert result.merge_state == "clean"
    assert result.ci == "none"
