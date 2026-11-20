from collections.abc import Mapping, Sequence
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


def status_answer(
    *,
    state: str = "OPEN",
    mergeable: str = "MERGEABLE",
    merge_state: str = "BLOCKED",
    sha: str = "a" * 40,
    runs: Sequence[Mapping[str, object]] = (),
    statuses: Sequence[Mapping[str, str]] = (),
    reviews: Sequence[Mapping[str, object]] = (),
    resolved: Sequence[bool] = (),
    unreadable: Sequence[str] = (),
    **fields: object,
) -> dict[str, object]:
    """GitHub's answer to the status query; ``runs`` are shaped as REST lists them."""
    suites: dict[object, dict[str, object]] = {}
    for index, run in enumerate(runs):
        suite = run.get("check_suite")
        suite_id = suite["id"] if isinstance(suite, dict) else -index
        app = run.get("app") if isinstance(run.get("app"), dict) else {}
        node = suites.setdefault(
            suite_id,
            {
                "databaseId": suite_id,
                "app": {"databaseId": app.get("id"), "slug": app.get("slug")},
                "workflowRun": run.get("workflow"),
                "checkRuns": {"pageInfo": {"hasNextPage": False}, "nodes": []},
            },
        )
        conclusion = run.get("conclusion")
        node["checkRuns"]["nodes"].append(
            {
                "databaseId": run.get("id", index + 1),
                "name": run.get("name"),
                "status": str(run.get("status")).upper(),
                "conclusion": conclusion.upper() if isinstance(conclusion, str) else None,
                "detailsUrl": run.get("details_url"),
                "url": run.get("html_url"),
            }
        )
    pull: dict[str, object] = {
        "title": "Live",
        "state": state,
        "isDraft": False,
        "mergeable": mergeable,
        "mergeStateStatus": merge_state,
        "author": {"__typename": "User", "login": "octocat"},
        "autoMergeRequest": None,
        "headRefName": "feature",
        "headRefOid": sha,
        "baseRefName": "main",
        "reviewDecision": None,
        "reviews": {"pageInfo": {"hasNextPage": False}, "nodes": list(reviews)},
        "reviewThreads": {
            "pageInfo": {"hasNextPage": False, "endCursor": None},
            "nodes": [{"isResolved": flag} for flag in resolved],
        },
        "commits": {
            "nodes": [
                {
                    "commit": {
                        "oid": sha,
                        "status": {
                            "contexts": [
                                {
                                    "context": status["context"],
                                    "state": status["state"].upper(),
                                    "targetUrl": status.get("target_url"),
                                }
                                for status in statuses
                            ]
                        }
                        if statuses
                        else None,
                        "checkSuites": {
                            "pageInfo": {"hasNextPage": False},
                            "nodes": list(suites.values()),
                        },
                    }
                }
            ]
        },
        **fields,
    }
    for field in unreadable:
        pull[field] = None
    answer: dict[str, object] = {"data": {"repository": {"pullRequest": pull}}}
    if unreadable:
        answer["errors"] = [
            {"path": ["repository", "pullRequest", field], "message": "Resource not accessible"}
            for field in unreadable
        ]
    return answer


def serve_status(monkeypatch, *answers: dict[str, object]) -> list[str]:
    """Answer each GraphQL query with the next of ``answers``; returns the queries' names."""
    queries: list[str] = []
    pending = list(answers)

    async def request(_client, method, url, **kwargs):
        assert (method, url) == ("POST", github_http.GITHUB_GRAPHQL)
        queries.append(kwargs["json"]["query"].split("(")[0].split()[-1])
        return response(pending.pop(0) if len(pending) > 1 else pending[0])

    monkeypatch.setattr(github_http, "github_request", request)
    return queries


def _reviewer(
    login: str, state: str, *, review_id: int = 1, user_id: int = 10, typename: str = "User"
):
    return {
        "fullDatabaseId": str(review_id),
        "state": state.upper(),
        "author": {
            "__typename": typename,
            "login": login,
            "databaseId": user_id,
            "avatarUrl": None,
        },
    }


@pytest.mark.parametrize(
    ("scope", "qualifier"), [("mine", "author"), ("review-requested", "review-requested")]
)
async def test_open_prs_use_live_state_current_head_and_legacy_statuses(
    monkeypatch, scope, qualifier
):
    queries = []

    async def request(_client, method, url, **kwargs):
        if method == "POST":
            n = kwargs["json"]["variables"]["number"]
            if n == 3:
                return response({"message": "Resource not accessible"}, 403)
            return response(
                status_answer(
                    state="MERGED" if n == 2 else "OPEN",
                    sha=str(n) * 40,
                    runs=[{"name": "unit", "status": "in_progress"}] if n == 1 else [],
                    statuses=[{"context": "legacy-ci", "state": "failure"}] if n == 1 else [],
                    resolved=[False, True],
                    reviews=[_reviewer("robin-agent", "approved", typename="Bot")],
                    title=f"Current {n}",
                    additions=n,
                    deletions=4,
                    createdAt="2026-09-01T00:00:00Z",
                    updatedAt="2026-09-12T00:00:00Z",
                    author={"__typename": "Bot", "login": "dependabot"},
                )
            )
        assert method == "GET" and url.endswith("/search/issues")
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

    monkeypatch.setattr(github_http, "github_request", request)
    monkeypatch.setattr(prs.BranchConfig, "read", AsyncMock(return_value=None))
    result = await prs.list_open_pull_requests(
        github_http.GitHubClient(MagicMock()), "octocat", "acme/app", scope=scope
    )
    assert queries == [f"is:pr is:open {qualifier}:octocat repo:acme/app"]
    assert [pr.number for pr in result.pull_requests] == [1, 3, 4]
    live, unavailable, no_checks = result.pull_requests
    assert live.title == "Current 1"
    assert live.author == "dependabot[bot]"
    assert live.ci == "failing"
    assert live.failing_checks == ["legacy-ci"]
    assert live.pending_checks == ["unit"]
    assert live.mergeable is True and live.merge_state == "blocked"
    assert live.unresolved_threads == 1
    assert live.reviewers == [
        prs.PullRequestReviewer(user_id=10, login="robin-agent[bot]", state="approved")
    ]
    assert (live.additions, live.deletions) == (1, 4)
    assert live.updated_at == "2026-09-12T00:00:00Z"
    assert unavailable.status_available is False and unavailable.ci == "unknown"
    assert unavailable.title == "PR 3"
    assert no_checks.ci == "none"


@pytest.mark.parametrize("repo", ["acme/app is:closed", "acme/../secrets", "acme/.."])
async def test_repository_filter_cannot_change_query_or_path(repo):
    with pytest.raises(HTTPException) as error:
        await prs.list_open_pull_requests(github_http.GitHubClient(MagicMock()), "octocat", repo)
    assert error.value.status_code == 422


@pytest.mark.parametrize(
    ("merge_state", "runs", "missing", "required_passing"),
    [
        ("BLOCKED", [{"name": "unit", "status": "completed"}], ["lint"], False),
        ("BLOCKED", [{"name": "unit", "status": "in_progress"}], [], False),
        ("CLEAN", [{"name": "unit", "status": "completed"}], [], True),
    ],
)
async def test_blocked_merge_names_required_checks_the_head_never_reported(
    monkeypatch, merge_state, runs, missing, required_passing
):
    serve_status(
        monkeypatch,
        status_answer(
            merge_state=merge_state, runs=runs, reviews=[_reviewer("reviewer", "approved")]
        ),
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
    serve_status(monkeypatch, status_answer(reviews=[_reviewer("bot", state)]))
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
    monkeypatch.setattr(
        prs.BranchConfig, "read", AsyncMock(return_value=BranchRules({RequiredCheck("unit")}, True))
    )
    # Three runs of one workflow's job: only the latest run counts.
    runs = [
        {
            "id": suite * 10,
            "name": "unit",
            "status": "completed" if result else "queued",
            "conclusion": result,
            "app": {"id": 15368, "slug": "github-actions"},
            "check_suite": {"id": suite},
            "workflow": {"event": "pull_request", "workflow": {"databaseId": 100}},
        }
        for suite, result in [(10, "failure"), (20, "success"), (30, conclusion)]
    ]
    serve_status(monkeypatch, status_answer(runs=runs))
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
    runs = []
    if conclusion != "absent":
        runs.append(
            {
                "name": "Robin Approves",
                "status": "completed" if conclusion else "queued",
                "conclusion": conclusion,
                "html_url": "https://github.com/acme/app/runs/1",
                "app": {"id": 42},
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
    serve_status(
        monkeypatch,
        status_answer(merge_state="BLOCKED" if required else "UNSTABLE", runs=runs),
    )
    head_ci = prs.PullRequestCI(
        state="open",
        merged=False,
        head_sha="a" * 40,
        head_ref="",
        base_ref="main",
        checks=github_http.CommitCI(runs, []),
    )
    monkeypatch.setattr(prs.PullRequestClient, "ci", AsyncMock(return_value=head_ci))
    monkeypatch.setattr(prs.PullRequestClient, "head_sha", AsyncMock(return_value="a" * 40))
    monkeypatch.setattr(github_http.RepoClient, "check_runs", AsyncMock(return_value=runs))
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
    serve_status(monkeypatch, status_answer(merge_state="CLEAN", unreadable=["commits"]))
    monkeypatch.setattr(
        github_http.RepoClient, "check_runs", AsyncMock(side_effect=ValueError("Unavailable"))
    )
    monkeypatch.setattr(github_http.RepoClient, "commit_statuses", AsyncMock(return_value=[]))
    result = await _client(1).load()
    assert result is not None
    assert result.required_checks_passing is True
    assert result.ci == "unknown"


async def test_checks_past_one_page_are_read_in_full_over_rest(monkeypatch):
    answer = status_answer()
    commit = answer["data"]["repository"]["pullRequest"]["commits"]["nodes"][0]["commit"]
    commit["checkSuites"]["pageInfo"]["hasNextPage"] = True
    serve_status(monkeypatch, answer)
    monkeypatch.setattr(
        github_http.RepoClient,
        "check_runs",
        AsyncMock(return_value=[{"name": "unit", "status": "completed", "conclusion": "failure"}]),
    )
    monkeypatch.setattr(github_http.RepoClient, "commit_statuses", AsyncMock(return_value=[]))
    monkeypatch.setattr(prs.BranchConfig, "read", AsyncMock(return_value=None))
    result = await _client(1).load()
    assert result is not None
    assert result.failing_checks == ["unit"]


async def test_a_forbidden_workflow_hides_no_checks(monkeypatch):
    # Two runs of one job whose workflow GitHub would not name: both are kept.
    runs = [
        {
            "id": suite,
            "name": "unit",
            "status": status,
            "conclusion": conclusion,
            "app": {"id": 15368, "slug": "github-actions"},
            "check_suite": {"id": suite},
        }
        for suite, status, conclusion in [(1, "completed", "failure"), (2, "queued", None)]
    ]
    answer = status_answer(runs=runs)
    answer["errors"] = [
        {
            "type": "FORBIDDEN",
            "message": "Resource not accessible by integration",
            "path": [
                "repository",
                "pullRequest",
                "commits",
                "nodes",
                0,
                "commit",
                "checkSuites",
                "nodes",
                index,
                "workflowRun",
            ],
        }
        for index in range(2)
    ]
    serve_status(monkeypatch, answer)
    rest = AsyncMock(side_effect=AssertionError("checks were read over GraphQL"))
    monkeypatch.setattr(github_http.RepoClient, "check_runs", rest)
    monkeypatch.setattr(github_http.RepoClient, "commit_statuses", rest)
    monkeypatch.setattr(prs.BranchConfig, "read", AsyncMock(return_value=None))
    result = await _client(1).load()
    thread = await _client(1).thread_status()
    assert result is not None
    assert (result.failing_checks, result.pending_checks) == (["unit"], ["unit"])
    assert thread["checksAvailable"] is True
    assert [check["name"] for check in thread["failingChecks"]] == ["unit"]
    assert thread["pendingCheckCount"] == 1


async def test_mergeability_is_not_awaited_forever(monkeypatch):
    monkeypatch.setattr(prs, "_MERGEABILITY_DELAY_SECONDS", 0)
    monkeypatch.setattr(prs.BranchConfig, "read", AsyncMock(return_value=None))
    queries = serve_status(monkeypatch, status_answer(mergeable="UNKNOWN", merge_state="UNKNOWN"))
    result = await _client(1).load(wanted={"open"})
    assert queries[: prs._MERGEABILITY_ATTEMPTS] == [
        "PullRequestStatus",
        *["PullRequestMergeability"] * (prs._MERGEABILITY_ATTEMPTS - 1),
    ]
    assert result is not None
    assert result.status_available is True and result.mergeable is None
    assert result.merge_state == "unknown"


async def test_mergeability_settles_on_a_later_read(monkeypatch):
    monkeypatch.setattr(prs, "_MERGEABILITY_DELAY_SECONDS", 0)
    monkeypatch.setattr(prs.BranchConfig, "read", AsyncMock(return_value=None))
    settled = {
        "data": {
            "repository": {"pullRequest": {"mergeable": "CONFLICTING", "mergeStateStatus": "DIRTY"}}
        }
    }
    serve_status(monkeypatch, status_answer(mergeable="UNKNOWN", merge_state="UNKNOWN"), settled)
    result = await _client(1).load(wanted={"open"})
    assert result is not None
    assert (result.mergeable, result.merge_state) == (False, "dirty")


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
    assert found.next_page is None


async def test_a_plugins_search_reads_one_page_when_asked(monkeypatch):
    listing = AsyncMock(
        return_value=prs.OpenPullRequests(
            pull_requests=[prs.OpenPullRequest(repo="acme/app", number=2, title="")],
            next_page=3,
            incomplete=False,
            updated_at="2026-01-01T00:00:00Z",
        )
    )
    monkeypatch.setattr(plugin_routes, "list_open_pull_requests", listing)
    monkeypatch.setattr(github_http.GitHubClient, "as_user", Mock(return_value=MagicMock()))
    plugin = Mock(readable_repositories=AsyncMock(return_value=["acme/app"]))
    plugin.user.github_login = "octocat"

    found = await plugin_routes.api_plugin_search_pulls(plugin, "author", page=2)

    assert [pull.number for pull in found.pulls] == [2]
    assert found.next_page == 3
    assert listing.await_count == 1
    assert listing.await_args.kwargs["page"] == 2


async def _reviewers(monkeypatch, first, *later):
    """What ``load`` reports of the reviews GitHub lists in pages ``first`` and ``later``."""
    answer = status_answer(reviews=first)
    if later:
        reviews = answer["data"]["repository"]["pullRequest"]["reviews"]
        reviews["pageInfo"] = {"hasNextPage": True, "endCursor": "page-1"}
    pages = [
        {
            "data": {
                "repository": {
                    "pullRequest": {
                        "reviews": {
                            "pageInfo": {
                                "hasNextPage": index + 1 < len(later),
                                "endCursor": f"page-{index + 2}",
                            },
                            "nodes": page,
                        }
                    }
                }
            }
        }
        for index, page in enumerate(later)
    ]
    serve_status(monkeypatch, answer, *pages)
    monkeypatch.setattr(prs.BranchConfig, "read", AsyncMock(return_value=None))
    result = await _client(1).load()
    assert result is not None and result.reviewers is not None
    return [(reviewer.login, reviewer.state) for reviewer in result.reviewers], (
        result.review_decision
    )


async def test_review_decision_uses_latest_active_decision_per_reviewer(monkeypatch):
    assert await _reviewers(
        monkeypatch,
        [
            _reviewer("reviewer", "changes_requested", review_id=1),
            _reviewer("reviewer", "approved", review_id=2),
        ],
        [
            _reviewer("reviewer", "commented", review_id=3),
            _reviewer("other", "dismissed", review_id=4, user_id=20),
        ],
    ) == ([("reviewer", "approved"), ("other", "dismissed")], "approved")
    _, decision = await _reviewers(
        monkeypatch,
        [
            _reviewer("reviewer", "approved", review_id=1),
            _reviewer("reviewer", "dismissed", review_id=2),
        ],
    )
    assert decision == "none"
    _, decision = await _reviewers(
        monkeypatch,
        [
            _reviewer("reviewer", "approved", review_id=1),
            _reviewer("other", "changes_requested", review_id=2, user_id=20),
        ],
    )
    assert decision == "changes_requested"


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


async def test_unreadable_review_threads_leave_only_the_unresolved_count_unknown(monkeypatch):
    serve_status(
        monkeypatch,
        status_answer(
            merge_state="CLEAN",
            reviews=[_reviewer("reviewer", "approved")],
            unreadable=["reviewThreads"],
        ),
    )
    result = await _client(7).load(wanted={"open"})
    assert result is not None
    assert result.unresolved_threads is None
    assert result.review_decision == "approved"
    assert result.head_sha == "a" * 40
    assert result.merge_state == "clean"
    assert result.ci == "none"
