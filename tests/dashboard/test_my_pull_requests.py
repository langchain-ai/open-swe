from unittest.mock import AsyncMock, MagicMock

import httpx2
import pytest
from fastapi import HTTPException

from agent.dashboard import profiles
from agent.github import http as github_http
from agent.github import pull_request_dashboard_routes as pr_routes
from agent.github import pull_request_status as prs
from agent.github.ci import RequiredCheck
from agent.review import routes as review_routes


def _client(number: int) -> prs.PullRequestClient:
    return github_http.GitHubClient(MagicMock()).repo("acme", "app").pull_request(number)


def response(payload, status=200):
    return httpx2.Response(
        status, json=payload, request=httpx2.Request("GET", "https://api.github.com")
    )


async def test_open_prs_use_live_state_current_head_and_legacy_statuses(monkeypatch):
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

    monkeypatch.setattr(github_http, "github_request", request)
    result = await prs.list_open_pull_requests(
        github_http.GitHubClient(MagicMock()), "octocat", "acme/app"
    )
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
        await prs.list_open_pull_requests(github_http.GitHubClient(MagicMock()), "octocat", repo)
    assert error.value.status_code == 422


@pytest.mark.parametrize(
    ("merge_state", "runs", "missing", "reads_rules"),
    [
        ("blocked", [{"name": "unit", "status": "completed"}], ["lint"], True),
        ("blocked", [{"name": "unit", "status": "in_progress"}], [], False),
        ("clean", [{"name": "unit", "status": "completed"}], [], False),
    ],
)
async def test_blocked_merge_names_required_checks_the_head_never_reported(
    monkeypatch, merge_state, runs, missing, reads_rules
):
    pull = {
        "state": "open",
        "mergeable": True,
        "mergeable_state": merge_state,
        "head": {"sha": "a" * 40},
        "base": {"ref": "main"},
    }
    monkeypatch.setattr(prs.PullRequestClient, "pull", AsyncMock(return_value=pull))
    monkeypatch.setattr(github_http.RepoClient, "check_runs", AsyncMock(return_value=runs))
    monkeypatch.setattr(github_http.RepoClient, "commit_statuses", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        prs.PullRequestClient, "review_decision", AsyncMock(return_value="approved")
    )
    monkeypatch.setattr(
        prs.PullRequestClient, "review_state", AsyncMock(return_value=prs.ReviewState(0, False))
    )
    rules = AsyncMock(return_value={RequiredCheck("unit"), RequiredCheck("lint")})
    monkeypatch.setattr(prs, "read_required_checks", rules)
    result = await _client(1).load_open()
    assert result is not None
    assert result.missing_checks == missing
    assert rules.await_count == int(reads_rules)


async def test_mergeability_is_not_awaited_forever(monkeypatch):
    monkeypatch.setattr(prs, "_MERGEABILITY_DELAY_SECONDS", 0)
    fetches = AsyncMock(
        return_value={"state": "open", "mergeable": None, "mergeable_state": "unknown"}
    )
    monkeypatch.setattr(prs.PullRequestClient, "pull", fetches)
    monkeypatch.setattr(prs.PullRequestClient, "review_decision", AsyncMock(return_value="none"))
    result = await _client(1).load_open()
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
    monkeypatch.setattr(profiles, "get_valid_access_token", token)
    monkeypatch.setattr(pr_routes, "list_open_pull_requests", listing)
    await pr_routes.api_list_pull_requests(repo="acme/app", session={"sub": "octocat"})
    token.assert_awaited_once_with("octocat")
    github, *args = listing.await_args.args
    assert github.http.headers["Authorization"] == "Bearer user-token"
    assert args == ["octocat", "acme/app"]
    token.return_value = None
    with pytest.raises(github_http.GitHubSignInRequired):
        await pr_routes.api_list_pull_requests(session={"sub": "another-user"})
    assert listing.await_count == 1


async def test_review_decision_uses_latest_active_decision_per_reviewer(monkeypatch):
    monkeypatch.setattr(
        github_http,
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
    assert await _client(1).review_decision() == "approved"
    monkeypatch.setattr(
        github_http,
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
    assert await _client(1).review_decision() == "none"
    monkeypatch.setattr(
        github_http,
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
    assert await _client(1).review_decision() == "changes_requested"


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


def _patch_detail_fetchers(monkeypatch):
    monkeypatch.setattr(
        prs.PullRequestClient,
        "pull",
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
    monkeypatch.setattr(github_http.RepoClient, "check_runs", AsyncMock(return_value=[]))
    monkeypatch.setattr(github_http.RepoClient, "commit_statuses", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        prs.PullRequestClient, "review_decision", AsyncMock(return_value="approved")
    )
    monkeypatch.setattr(github_http, "GITHUB_GRAPHQL", "https://fake-gh/graphql")


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


async def test_a_graphql_failure_leaves_the_unresolved_count_unknown(monkeypatch):
    _patch_detail_fetchers(monkeypatch)
    monkeypatch.setattr(
        github_http,
        "github_request",
        AsyncMock(return_value=response({"errors": [{"message": "Bad credentials"}]})),
    )
    result = await _client(7).load_open()
    assert result is not None
    assert result.unresolved_threads is None
    assert result.review_decision == "approved"
    assert result.head_sha == "a" * 40
    assert result.merge_state == "clean"
    assert result.ci == "none"
