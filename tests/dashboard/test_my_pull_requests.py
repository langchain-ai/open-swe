from unittest.mock import AsyncMock, MagicMock, Mock

import httpx2
import pytest
from fastapi import HTTPException

from openswe.dashboard import profiles
from openswe.github import http as github_http
from openswe.github import pull_request_dashboard_routes as pr_routes
from openswe.github import pull_request_status as prs
from openswe.github.branch_config import BranchRules
from openswe.github.ci import RequiredCheck
from openswe.plugins import routes as plugin_routes
from openswe.review import routes as review_routes
from openswe.review.reviews import ReviewCheck


def _client(number: int) -> prs.PullRequestClient:
    return github_http.GitHubClient(MagicMock()).repo("acme", "app").pull_request(number)


def response(payload, status=200):
    return httpx2.Response(
        status, json=payload, request=httpx2.Request("GET", "https://api.github.com")
    )


@pytest.mark.parametrize(
    ("scope", "qualifier"), [("mine", "author"), ("review-requested", "review-requested")]
)
async def test_open_prs_use_live_state_current_head_and_legacy_statuses(
    monkeypatch, scope, qualifier
):
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
        github_http.GitHubClient(MagicMock()), "octocat", "acme/app", scope=scope
    )
    assert queries == [f"is:pr is:open {qualifier}:octocat repo:acme/app"]
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
    ("merge_state", "runs", "missing", "required_passing"),
    [
        ("blocked", [{"name": "unit", "status": "completed"}], ["lint"], False),
        ("blocked", [{"name": "unit", "status": "in_progress"}], [], False),
        ("clean", [{"name": "unit", "status": "completed"}], [], True),
    ],
)
async def test_blocked_merge_names_required_checks_the_head_never_reported(
    monkeypatch, merge_state, runs, missing, required_passing
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
        prs.PullRequestClient,
        "reviewers",
        AsyncMock(
            return_value=[prs.PullRequestReviewer(user_id=10, login="reviewer", state="approved")]
        ),
    )
    monkeypatch.setattr(
        prs.PullRequestClient, "review_state", AsyncMock(return_value=prs.ReviewState(0, False))
    )
    rules = AsyncMock(
        return_value=BranchRules({RequiredCheck("unit"), RequiredCheck("lint")}, True)
    )
    monkeypatch.setattr(prs.BranchConfig, "read", rules)
    result = await _client(1).load(wanted={"open"})
    assert result is not None
    assert result.missing_checks == missing
    assert result.required_checks_passing is required_passing


@pytest.mark.parametrize(("state", "review_required"), [("commented", True), ("approved", False)])
async def test_ruleset_approval_is_required_when_github_omits_the_review_decision(
    monkeypatch, state, review_required
):
    pull = {
        "state": "open",
        "mergeable": True,
        "mergeable_state": "blocked",
        "head": {"sha": "a" * 40},
        "base": {"ref": "main"},
    }
    monkeypatch.setattr(prs.PullRequestClient, "pull", AsyncMock(return_value=pull))
    monkeypatch.setattr(github_http.RepoClient, "check_runs", AsyncMock(return_value=[]))
    monkeypatch.setattr(github_http.RepoClient, "commit_statuses", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        prs.PullRequestClient,
        "reviewers",
        AsyncMock(return_value=[prs.PullRequestReviewer(user_id=10, login="bot", state=state)]),
    )
    monkeypatch.setattr(
        prs.PullRequestClient, "review_state", AsyncMock(return_value=prs.ReviewState(0, False))
    )
    monkeypatch.setattr(
        prs.BranchConfig, "read", AsyncMock(return_value=BranchRules(set(), True, 1))
    )
    result = await _client(1).load(wanted={"open"})
    assert result is not None
    assert result.review_required is review_required


@pytest.mark.parametrize(
    ("conclusion", "ci", "failures", "inconclusive", "required_passing"),
    [
        ("cancelled", "unknown", [], 1, False),
        ("stale", "unknown", [], 1, False),
        ("failure", "failing", ["unit"], 0, False),
        ("success", "passing", [], 0, True),
        ("neutral", "passing", [], 1, True),
        ("skipped", "passing", [], 1, True),
        (None, "pending", [], 0, False),
    ],
)
async def test_review_and_thread_status_agree_on_current_check_outcomes(
    monkeypatch, conclusion, ci, failures, inconclusive, required_passing
):
    pull = {
        "state": "open",
        "draft": False,
        "mergeable": True,
        "mergeable_state": "blocked",
        "head": {"sha": "a" * 40},
        "base": {"ref": "main"},
    }
    monkeypatch.setattr(
        prs.BranchConfig, "read", AsyncMock(return_value=BranchRules({RequiredCheck("unit")}, True))
    )
    runs = [
        {
            "id": suite * 10,
            "name": "unit",
            "status": "completed" if result else "queued",
            "conclusion": result,
            "app": {"id": 15368, "slug": "github-actions"},
            "check_suite": {"id": suite},
        }
        for suite, result in [(10, "failure"), (20, "success"), (30, conclusion)]
    ]
    workflows = [
        {
            "id": run_id,
            "check_suite_id": suite,
            "workflow_id": 100,
            "event": "pull_request",
            "run_attempt": 2 if suite == 30 else 1,
        }
        for suite, run_id in [(10, 10), (20, 20), (30, 5)]
    ]
    monkeypatch.setattr(prs.PullRequestClient, "pull", AsyncMock(return_value=pull))
    monkeypatch.setattr(
        github_http.RepoClient, "pages", AsyncMock(side_effect=[runs, workflows] * 2)
    )
    monkeypatch.setattr(github_http.RepoClient, "commit_statuses", AsyncMock(return_value=[]))
    monkeypatch.setattr(prs.PullRequestClient, "reviewers", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        prs.PullRequestClient, "review_state", AsyncMock(return_value=prs.ReviewState(0))
    )
    monkeypatch.setattr(prs.PullRequestClient, "unresolved_threads", AsyncMock(return_value=[]))
    review = await _client(1).load()
    thread = await _client(1).thread_status()
    assert review is not None
    assert review.ci == ci
    assert review.required_checks_passing is required_passing
    assert review.failing_checks == failures
    assert [check["name"] for check in thread["failingChecks"]] == failures
    assert thread["inconclusiveCheckCount"] == inconclusive
    assert len(review.pending_checks) == thread["pendingCheckCount"] == int(conclusion is None)


@pytest.mark.parametrize(
    ("required", "conclusion", "unit", "review_required", "ci", "required_passing"),
    [
        (True, "failure", None, True, "none", False),
        (True, "success", "success", False, "passing", True),
        (True, None, "success", True, "passing", False),
        (True, "cancelled", "success", True, "passing", False),
        (True, "absent", "success", True, "passing", False),
        (True, "failure", "failure", True, "failing", False),
        (True, "failure", "queued", True, "pending", False),
        (False, "failure", "success", False, "passing", True),
        (None, "failure", "success", False, "passing", None),
    ],
)
async def test_approval_is_separate_from_ci_and_only_required_by_branch_rules(
    monkeypatch, required, conclusion, unit, review_required, ci, required_passing
):
    pull = {
        "state": "open",
        "mergeable": True,
        "mergeable_state": "blocked" if required else "unstable",
        "head": {"sha": "a" * 40},
        "base": {"ref": "main"},
    }
    runs = []
    if conclusion != "absent":
        runs.append(
            {
                "name": "Robin Approves",
                "status": "completed" if conclusion else "queued",
                "conclusion": conclusion,
                "html_url": "https://github.com/acme/app/runs/1",
            }
        )
    if unit:
        runs.append(
            {
                "name": "unit",
                "status": "queued" if unit == "queued" else "completed",
                "conclusion": None if unit == "queued" else unit,
            }
        )
    monkeypatch.setattr(prs.PullRequestClient, "pull", AsyncMock(return_value=pull))
    monkeypatch.setattr(github_http.RepoClient, "check_runs", AsyncMock(return_value=runs))
    monkeypatch.setattr(github_http.RepoClient, "commit_statuses", AsyncMock(return_value=[]))
    monkeypatch.setattr(prs.PullRequestClient, "reviewers", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        prs.PullRequestClient, "review_state", AsyncMock(return_value=prs.ReviewState(0))
    )
    monkeypatch.setattr(prs.PullRequestClient, "unresolved_threads", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        prs.BranchConfig,
        "read",
        AsyncMock(
            return_value=BranchRules(
                {RequiredCheck("Robin Approves", app_id=42)} if required else set(), True
            )
            if required is not None
            else None
        ),
    )
    if runs and conclusion != "absent":
        runs[0]["app"] = {"id": 42}
    client = _client(1)
    review = await client.load()
    thread = await client.thread_status()
    checks = await ReviewCheck.on_head(client)
    assert review is not None
    assert review.review_required is review_required
    assert review.ci == ci
    assert review.required_checks_passing is required_passing
    assert review.missing_checks == []
    failures = ["unit"] if unit == "failure" else []
    assert review.failing_checks == failures
    assert [check["name"] for check in thread["failingChecks"]] == failures
    assert len(review.pending_checks) == thread["pendingCheckCount"] == int(unit == "queued")
    assert thread["inconclusiveCheckCount"] == 0
    assert [check.name for check in checks] == (["unit"] if unit else [])


async def test_clean_merge_confirms_required_checks_when_details_are_unavailable(monkeypatch):
    pull = {
        "state": "open",
        "mergeable": True,
        "mergeable_state": "clean",
        "head": {"sha": "a" * 40},
        "base": {"ref": "main"},
    }
    monkeypatch.setattr(prs.PullRequestClient, "pull", AsyncMock(return_value=pull))
    monkeypatch.setattr(
        github_http.RepoClient,
        "check_runs",
        AsyncMock(side_effect=httpx2.HTTPError("Unavailable")),
    )
    monkeypatch.setattr(github_http.RepoClient, "commit_statuses", AsyncMock(return_value=[]))
    monkeypatch.setattr(prs.PullRequestClient, "reviewers", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        prs.PullRequestClient, "review_state", AsyncMock(return_value=prs.ReviewState(0))
    )
    result = await _client(1).load()
    assert result is not None
    assert result.required_checks_passing is True


async def test_mergeability_is_not_awaited_forever(monkeypatch):
    monkeypatch.setattr(prs, "_MERGEABILITY_DELAY_SECONDS", 0)
    fetches = AsyncMock(
        return_value={"state": "open", "mergeable": None, "mergeable_state": "unknown"}
    )
    monkeypatch.setattr(prs.PullRequestClient, "pull", fetches)
    monkeypatch.setattr(prs.PullRequestClient, "reviewers", AsyncMock(return_value=[]))
    result = await _client(1).load(wanted={"open"})
    assert fetches.await_count == prs._MERGEABILITY_ATTEMPTS
    assert result is not None
    assert result.status_available is True and result.mergeable is None


@pytest.mark.parametrize("scope", ["mine", "review-requested"])
async def test_route_uses_signed_in_user_token_and_rejects_missing_auth(monkeypatch, scope):
    token = AsyncMock(return_value="user-token")
    listing = AsyncMock(
        return_value=prs.OpenPullRequests(
            pull_requests=[], next_page=None, incomplete=False, updated_at="2026-01-01T00:00:00Z"
        )
    )
    monkeypatch.setattr(profiles, "get_valid_access_token", token)
    monkeypatch.setattr(pr_routes, "list_open_pull_requests", listing)
    await pr_routes.api_list_pull_requests(repo="acme/app", scope=scope, session={"sub": "octocat"})
    token.assert_awaited_once_with("octocat")
    github, *args = listing.await_args.args
    assert github.http.headers["Authorization"] == "Bearer user-token"
    assert args == ["octocat", "acme/app"]
    assert listing.await_args.kwargs["scope"] == scope
    token.return_value = None
    with pytest.raises(github_http.GitHubSignInRequired):
        await pr_routes.api_list_pull_requests(session={"sub": "another-user"})
    assert listing.await_count == 1


@pytest.mark.parametrize("last_page", ["read", "timed-out", "failed"])
async def test_a_plugins_search_reads_past_githubs_first_page(monkeypatch, last_page):
    def page(numbers, next_page, incomplete=False):
        return prs.OpenPullRequests(
            pull_requests=[
                prs.OpenPullRequest(repo="acme/app", number=n, title="") for n in numbers
            ],
            next_page=next_page,
            incomplete=incomplete,
            updated_at="2026-01-01T00:00:00Z",
        )

    # A search that times out answers with nothing; one GitHub refuses, as past its rate
    # limit, raises (``list_open_pull_requests``).
    last = {
        "read": page([3], None),
        "timed-out": page([], None, incomplete=True),
        "failed": HTTPException(502, "Could not load open PRs from GitHub"),
    }[last_page]
    monkeypatch.setattr(
        plugin_routes,
        "list_open_pull_requests",
        AsyncMock(side_effect=[page([1], 2), page([2], 3), last]),
    )
    monkeypatch.setattr(github_http.GitHubClient, "as_user", Mock(return_value=MagicMock()))
    plugin = Mock(readable_repositories=AsyncMock(return_value=["acme/app"]))
    plugin.user.github_login = "octocat"

    found = await plugin_routes.api_plugin_search_pulls(plugin, "review-requested")

    assert [pull.number for pull in found.pulls] == ([1, 2, 3] if last_page == "read" else [1, 2])
    assert found.incomplete is (last_page != "read")


async def _decision() -> prs.ReviewDecision:
    reviewers = await _client(1).reviewers()
    assert reviewers is not None
    return prs.PullRequestReviewer.decision(reviewers)


async def test_review_decision_uses_latest_active_decision_per_reviewer(monkeypatch):
    monkeypatch.setattr(
        github_http,
        "github_request",
        AsyncMock(
            return_value=response(
                [
                    {
                        "id": 1,
                        "user": {"id": 10, "login": "reviewer"},
                        "state": "CHANGES_REQUESTED",
                    },
                    {"id": 2, "user": {"id": 10, "login": "reviewer"}, "state": "APPROVED"},
                    {"id": 3, "user": {"id": 10, "login": "reviewer"}, "state": "COMMENTED"},
                    {"id": 4, "user": {"id": 20, "login": "other"}, "state": "DISMISSED"},
                ]
            )
        ),
    )
    assert [
        (reviewer.login, reviewer.state) for reviewer in await _client(1).reviewers() or []
    ] == [("reviewer", "approved"), ("other", "dismissed")]
    assert await _decision() == "approved"
    monkeypatch.setattr(
        github_http,
        "github_request",
        AsyncMock(
            return_value=response(
                [
                    {"id": 1, "user": {"id": 10, "login": "reviewer"}, "state": "APPROVED"},
                    {"id": 2, "user": {"id": 10, "login": "reviewer"}, "state": "DISMISSED"},
                ]
            )
        ),
    )
    assert await _decision() == "none"
    monkeypatch.setattr(
        github_http,
        "github_request",
        AsyncMock(
            return_value=response(
                [
                    {"id": 1, "user": {"id": 10, "login": "reviewer"}, "state": "APPROVED"},
                    {"id": 2, "user": {"id": 20, "login": "other"}, "state": "CHANGES_REQUESTED"},
                ]
            )
        ),
    )
    assert await _decision() == "changes_requested"


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
        prs.PullRequestClient,
        "reviewers",
        AsyncMock(
            return_value=[prs.PullRequestReviewer(user_id=10, login="reviewer", state="approved")]
        ),
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
    result = await _client(7).load(wanted={"open"})
    assert result is not None
    assert result.unresolved_threads is None
    assert result.review_decision == "approved"
    assert result.head_sha == "a" * 40
    assert result.merge_state == "clean"
    assert result.ci == "none"
