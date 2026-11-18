"""Unit tests for GitHub CI read helpers used by the auto-fix flow."""

from pathlib import Path
from unittest.mock import MagicMock

import httpx2
import pytest

from openswe.github import ci as github_ci
from openswe.github import http as github_http
from openswe.github.http import GitHubClient, RepoClient


def _repo() -> RepoClient:
    return GitHubClient(MagicMock()).repo("o", "r")


def _response(url: str, payload: object, status: int = 200) -> httpx2.Response:
    return httpx2.Response(status, json=payload, request=httpx2.Request("GET", url))


async def test_required_checks_merge_branch_protection_and_every_ruleset_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    branch = {
        "protection": {
            "required_status_checks": {
                "contexts": ["lint"],
                "checks": [{"context": "unit tests", "app_id": 1}],
            }
        }
    }
    filler = [{"type": "pull_request", "parameters": {}}] * 99
    rules_pages = {
        "1": [
            *filler,
            {
                "type": "required_status_checks",
                "parameters": {
                    "required_status_checks": [
                        {"context": "e2e", "integration_id": -1},
                        {"context": github_ci.REVIEW_CHECK_RUN_NAME},
                        {"context": "Open SWE Review"},
                        {"context": "Robin Auto-fix"},
                        {"context": "Open SWE Auto-fix"},
                    ]
                },
            },
        ],
        "2": [
            {
                "type": "required_status_checks",
                "parameters": {"required_status_checks": [{"context": "deploy"}]},
            }
        ],
    }

    async def request(
        _client: object, _method: str, url: str, params: dict[str, str] | None = None, **_: object
    ) -> httpx2.Response:
        if "/rules/" in url:
            page = (params or {})["page"]
            response = _response(url, rules_pages[page])
            if page == "1":
                response.headers["Link"] = f'<{url}?page=2>; rel="next"'
            return response
        return _response(url, branch)

    monkeypatch.setattr(github_http, "github_request", request)

    required = await github_ci.RequiredCheck.for_branch(_repo(), "main")

    assert required == {
        github_ci.RequiredCheck("lint"),
        github_ci.RequiredCheck("unit tests", 1),
        github_ci.RequiredCheck("e2e"),
        github_ci.RequiredCheck("deploy"),
    }
    assert github_ci.unreported_required_checks(
        required,
        [{"name": "unit tests", "app": {"id": 2}}, {"name": "e2e", "app": {"id": 9}}],
        [{"context": "lint"}, {"context": "unit tests"}],
    ) == ["deploy", "unit tests"]


@pytest.mark.parametrize("rule_type", [None, "workflows", "code_scanning"])
async def test_required_checks_unavailable_is_none(
    monkeypatch: pytest.MonkeyPatch, rule_type: str | None
) -> None:
    async def request(_client: object, _method: str, url: str, **_: object) -> httpx2.Response:
        if rule_type is None:
            return _response(url, {"message": "boom"}, 500)
        return _response(
            url,
            [{"type": rule_type}]
            if "/rules/" in url
            else {"protection": {"required_status_checks": {"contexts": ["lint"]}}},
        )

    monkeypatch.setattr(github_http, "github_request", request)

    assert (
        await github_ci.RequiredCheck.for_branch(_repo(), "main", require_mappable_rules=True)
        is None
    )
    assert await github_ci.RequiredCheck.for_branch(_repo(), "main") == (
        None if rule_type is None else {github_ci.RequiredCheck("lint")}
    )


async def test_commit_statuses_keep_the_latest_per_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def request(_client: object, _method: str, url: str, **_: object) -> httpx2.Response:
        return _response(
            url,
            {
                "statuses": [
                    {"id": 2, "context": "ci", "state": "success"},
                    {"id": 1, "context": "ci", "state": "failure"},
                ]
            },
        )

    monkeypatch.setattr(github_http, "github_request", request)

    assert await _repo().commit_statuses("s") == [{"id": 2, "context": "ci", "state": "success"}]


async def test_write_permission_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    async def request(_client: object, _method: str, url: str, **_: object) -> httpx2.Response:
        return _response(url, {"message": "boom"}, 500)

    monkeypatch.setattr(github_http, "github_request", request)

    assert not await _repo().can_write("bob")
    assert not await _repo().can_write("")


def test_baby_sit_skill_names_every_robin_check() -> None:
    skill = (Path(__file__).parents[2] / "mda/bundled_skills/baby-sit/SKILL.md").read_text()

    assert [name for name in github_ci.OPEN_SWE_CHECK_NAMES if f"`{name}`" not in skill] == []


def test_commit_check_failures_are_the_failing_runs_and_statuses() -> None:
    checks = github_ci.CommitChecks(
        runs=[
            {"id": 1, "name": "lint", "status": "completed", "conclusion": "failure"},
            {"id": 2, "name": "tests", "status": "completed", "conclusion": "success"},
            {"id": 3, "name": "e2e", "status": "in_progress", "conclusion": None},
            {"id": 6, "name": "build", "status": "completed", "conclusion": "cancelled"},
            {"id": 7, "name": "lint", "status": "completed", "conclusion": "stale"},
        ],
        statuses=[
            {"id": 4, "context": "deploy", "state": "error"},
            {"id": 5, "context": "docs", "state": "success"},
        ],
    )

    assert checks.failures == [
        github_ci.CheckFailure(name="lint", conclusion="failure", url="", run_id=1),
        github_ci.CheckFailure(name="build", conclusion="cancelled", url="", run_id=6),
        github_ci.CheckFailure(name="deploy", conclusion="error", url="", run_id=None),
    ]


def _suite(
    suite_id: int,
    runs: list[dict[str, object]],
    *,
    slug: str = "github-actions",
    more: bool = False,
) -> dict[str, object]:
    actions = slug == "github-actions"
    return {
        "databaseId": suite_id,
        "app": {"databaseId": 15368 if actions else 1, "slug": slug},
        "workflowRun": {"event": "pull_request", "workflow": {"databaseId": 7}}
        if actions
        else None,
        "checkRuns": {"pageInfo": {"hasNextPage": more}, "nodes": runs},
    }


def _graphql_run(run_id: int, status: str, conclusion: str | None) -> dict[str, object]:
    return {
        "databaseId": run_id,
        "name": "tests" if run_id < 30 else "scan",
        "status": status,
        "conclusion": conclusion,
        "detailsUrl": f"https://ci.example/{run_id}",
        "url": f"https://github.com/o/r/runs/{run_id}",
    }


def test_commit_ci_reads_graphql_in_rest_terms_without_superseded_reruns() -> None:
    suites = [
        _suite(1, [_graphql_run(10, "COMPLETED", "TIMED_OUT")]),
        _suite(2, [_graphql_run(20, "IN_PROGRESS", None)]),
        _suite(3, [_graphql_run(30, "COMPLETED", "SUCCESS")], slug="socket"),
    ]
    commit = {
        "oid": "a" * 40,
        "status": {
            "contexts": [
                {"context": "deploy", "state": "FAILURE", "targetUrl": "https://d"},
                {"context": "docs", "state": "EXPECTED", "targetUrl": None},
            ]
        },
        "checkSuites": {"pageInfo": {"hasNextPage": False}, "nodes": suites},
    }

    ci = github_http.CommitCI.from_graphql(commit)

    assert ci is not None
    assert [
        (run["id"], run["status"], run["conclusion"], run["html_url"], run["app"]["id"])
        for run in ci.runs
    ] == [
        (20, "in_progress", None, "https://ci.example/20", 15368),
        (30, "completed", "success", "https://github.com/o/r/runs/30", 1),
    ]
    assert ci.statuses == [
        {"context": "deploy", "state": "failure", "target_url": "https://d"},
        {"context": "docs", "state": "pending", "target_url": None},
    ]
    assert github_ci.CommitChecks.of(ci).state == "failure"

    suites.append(_suite(4, [], more=True))
    assert github_http.CommitCI.from_graphql(commit) is None


def _withheld_workflows(path: list[object]) -> list[dict[str, object]]:
    """GitHub's errors for a token that reads Checks but not Actions."""
    return [
        {
            "type": "FORBIDDEN",
            "message": "Resource not accessible by integration",
            "path": [*path, "checkSuites", "nodes", index, "workflowRun"],
        }
        for index in (0, 1)
    ]


def _withheld_commit(sha: str) -> dict[str, object]:
    suites = [
        _suite(1, [_graphql_run(10, "COMPLETED", "FAILURE")]),
        _suite(2, [_graphql_run(20, "COMPLETED", "SUCCESS")]),
    ]
    for suite in suites:
        suite["workflowRun"] = None
    return {
        "oid": sha,
        "status": {"contexts": [{"context": "deploy", "state": "SUCCESS", "targetUrl": None}]},
        "checkSuites": {"pageInfo": {"hasNextPage": False}, "nodes": suites},
    }


async def test_ci_reads_keep_checks_when_github_withholds_workflow_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sha = "a" * 40
    commit = _withheld_commit(sha)
    answers = {
        "query CommitCI(": {
            "data": {"repository": {"object": commit}},
            "errors": _withheld_workflows(["repository", "object"]),
        },
        "query PullRequestCI(": {
            "data": {
                "repository": {
                    "pullRequest": {
                        "state": "OPEN",
                        "headRefOid": sha,
                        "headRefName": "feature",
                        "baseRefName": "main",
                        "commits": {"nodes": [{"commit": commit}]},
                    }
                }
            },
            "errors": _withheld_workflows(
                ["repository", "pullRequest", "commits", "nodes", 0, "commit"]
            ),
        },
    }
    urls: list[str] = []

    async def request(
        _client: object, _method: str, url: str, json: dict[str, str], **_: object
    ) -> httpx2.Response:
        urls.append(url)
        answer = next(body for name, body in answers.items() if name in json["query"])
        return _response(url, answer)

    monkeypatch.setattr(github_http, "github_request", request)
    repo = _repo()

    commit_ci = await repo.commit_ci(sha)
    pull_ci = await repo.pull_request(1).ci()

    for ci in (commit_ci, pull_ci.checks):
        assert [(run["id"], run["conclusion"]) for run in ci.runs] == [
            (10, "failure"),
            (20, "success"),
        ]
        assert [status["context"] for status in ci.statuses] == ["deploy"]
    assert urls == [github_http.GITHUB_GRAPHQL] * 2


async def test_commit_ci_raises_graphql_errors_outside_workflow_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sha = "a" * 40
    errors = [
        *_withheld_workflows(["repository", "object"]),
        {"type": "FORBIDDEN", "message": "denied", "path": ["repository", "object", "status"]},
    ]

    async def request(_client: object, _method: str, url: str, **_: object) -> httpx2.Response:
        return _response(
            url, {"data": {"repository": {"object": _withheld_commit(sha)}}, "errors": errors}
        )

    monkeypatch.setattr(github_http, "github_request", request)

    with pytest.raises(github_http.GraphQLError) as raised:
        await _repo().commit_ci(sha)
    assert raised.value.errors == errors
