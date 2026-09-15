from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import httpx2
import pytest
from fastapi import HTTPException

from agent.github import pull_request_dashboard_routes as pr_routes
from agent.github import pull_request_status as prs
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
    assert result["pullRequests"][0]["title"] == "PR 1"
    assert result["pullRequests"][0]["detailsLoading"] is True


async def test_open_prs_use_live_state_current_head_and_legacy_statuses(monkeypatch):
    monkeypatch.setattr(prs, "github_client", client)
    queries = []

    async def request(_client, method, url, **kwargs):
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
    assert [pr["number"] for pr in result["pullRequests"]] == [1, 3, 4]
    live, unavailable, no_checks = result["pullRequests"]
    assert live["ci"] == "failing"
    assert live["failingChecks"] == ["legacy-ci"]
    assert live["pendingChecks"] == ["unit"]
    assert live["mergeable"] is True and live["mergeState"] == "blocked"
    assert (live["additions"], live["deletions"]) == (1, 4)
    assert live["updatedAt"] == "2026-09-12T00:00:00Z"
    assert unavailable["statusAvailable"] is False and unavailable["ci"] == "unknown"
    assert no_checks["ci"] == "none"


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


def search_item(number):
    return {
        "number": number,
        "title": f"PR {number}",
        "repository_url": "https://api.github.com/repos/acme/app",
    }


async def test_search_pages_through_complete_results(monkeypatch):
    monkeypatch.setattr(prs, "github_client", client)
    search = AsyncMock(return_value=response({"items": [], "total_count": 250}))
    monkeypatch.setattr(prs, "github_request", search)
    result = await prs.list_open_pull_requests("octocat", "user-token")
    assert search.await_args.kwargs["params"]["page"] == "1"
    assert result["nextPage"] == 2 and result["incomplete"] is False
    result = await prs.list_open_pull_requests("octocat", "user-token", page=3)
    assert search.await_args.kwargs["params"]["page"] == "3"
    assert result["nextPage"] is None
    search.return_value = response({"items": [], "total_count": 5000})
    result = await prs.list_open_pull_requests("octocat", "user-token", page=10)
    assert result["nextPage"] is None
    with pytest.raises(HTTPException) as error:
        await prs.list_open_pull_requests("octocat", "user-token", page=11)
    assert error.value.status_code == 422


async def test_a_timed_out_search_is_retried_rather_than_shown_truncated(monkeypatch):
    """GitHub answers a timed-out search with an arbitrary subset of the matches."""
    monkeypatch.setattr(prs, "github_client", client)
    complete = {"items": [search_item(n) for n in range(1, 60)], "total_count": 59}
    search = AsyncMock(
        side_effect=[
            response({"items": [search_item(1)], "total_count": 3, "incomplete_results": True}),
            response(complete),
        ]
    )
    monkeypatch.setattr(prs, "github_request", search)
    result = await prs.list_open_pull_requests("octocat", "user-token", lightweight=True)
    assert search.await_count == 2
    assert len(result["pullRequests"]) == 59 and result["incomplete"] is False


async def test_an_always_incomplete_search_keeps_the_fullest_answer(monkeypatch):
    monkeypatch.setattr(prs, "github_client", client)
    incomplete = [
        response(
            {
                "items": [search_item(n) for n in range(1, count + 1)],
                "total_count": count,
                "incomplete_results": True,
            }
        )
        for count in (2, 7, 4)
    ]
    search = AsyncMock(side_effect=incomplete)
    monkeypatch.setattr(prs, "github_request", search)
    result = await prs.list_open_pull_requests("octocat", "user-token", lightweight=True)
    assert search.await_count == 3
    assert len(result["pullRequests"]) == 7 and result["incomplete"] is True
    assert result["nextPage"] is None


async def test_a_failed_retry_keeps_the_partial_answer(monkeypatch):
    monkeypatch.setattr(prs, "github_client", client)
    search = AsyncMock(
        side_effect=[
            response(
                {"items": [search_item(1)], "total_count": 1, "incomplete_results": True},
            ),
            httpx2.ConnectError("boom"),
        ]
    )
    monkeypatch.setattr(prs, "github_request", search)
    result = await prs.list_open_pull_requests("octocat", "user-token", lightweight=True)
    assert len(result["pullRequests"]) == 1 and result["incomplete"] is True


async def test_route_uses_signed_in_user_token_and_rejects_missing_auth(monkeypatch):
    token = AsyncMock(return_value="user-token")
    listing = AsyncMock(return_value={"pullRequests": []})
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
        pullRequests=[{"repo": "acme/private", "number": 1}]
    )
    assert await review_routes.api_get_review_summaries(payload, session={"sub": "octocat"}) == {}
    lookup.assert_not_awaited()
    payload = review_routes.ReviewSummariesRequest(
        pullRequests=[{"repo": "acme/allowed", "number": 1}, {"repo": "acme/private", "number": 2}]
    )
    await review_routes.api_get_review_summaries(payload, session={"sub": "octocat"})
    lookup.assert_awaited_once_with([("acme", "allowed", 1)])
