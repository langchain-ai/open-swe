"""Unit tests for GitHub CI read helpers used by the auto-fix flow."""

from unittest.mock import MagicMock

import httpx2
import pytest

from openswe.github import ci as github_ci
from openswe.github import http as github_http
from openswe.github.http import GitHubClient, RepoClient
from openswe.skill_store.run_skills import BUNDLED_SKILLS_DIR


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
    skill = (BUNDLED_SKILLS_DIR / "baby-sit" / "SKILL.md").read_text()

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
