"""Unit tests for GitHub CI read helpers used by the auto-fix flow."""

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
            return _response(url, rules_pages[(params or {})["page"]])
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


async def test_required_checks_unavailable_is_none(monkeypatch: pytest.MonkeyPatch) -> None:
    async def request(_client: object, _method: str, url: str, **_: object) -> httpx2.Response:
        return _response(url, {"message": "boom"}, 500)

    monkeypatch.setattr(github_http, "github_request", request)

    assert await github_ci.RequiredCheck.for_branch(_repo(), "main") is None


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
