"""Unit tests for GitHub CI read helpers used by the auto-fix flow."""

from typing import Any

import httpx2
import pytest

from openswe.github import ci as github_ci


class _FakeResponse:
    def __init__(self, payload: Any = None, error: bool = False) -> None:
        self._payload = payload if payload is not None else {}
        self._error = error
        self.status_code = 200
        self.headers: dict[str, str] = {}

    def raise_for_status(self) -> None:
        if self._error:
            raise httpx2.HTTPError("boom")

    def json(self) -> Any:
        return self._payload


class _FakeClient:
    response: _FakeResponse = _FakeResponse({})

    def __init__(self, **kwargs: Any) -> None:
        pass

    async def __aenter__(self) -> _FakeClient:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    async def get(self, url: str, **_: Any) -> _FakeResponse:
        return type(self).response


def _patch(monkeypatch: pytest.MonkeyPatch, payload: Any, error: bool = False) -> None:
    _FakeClient.response = _FakeResponse(payload, error=error)
    monkeypatch.setattr(github_ci.httpx2, "AsyncClient", _FakeClient)


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
    ) -> _FakeResponse:
        if "/rules/" in url:
            return _FakeResponse(rules_pages[(params or {})["page"]])
        return _FakeResponse(branch)

    monkeypatch.setattr(github_ci, "github_request", request)

    required = await github_ci.fetch_required_checks(owner="o", repo="r", branch="main", token="t")

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
    async def request(*_: object, **__: object) -> _FakeResponse:
        return _FakeResponse(error=True)

    monkeypatch.setattr(github_ci, "github_request", request)

    assert (
        await github_ci.fetch_required_checks(owner="o", repo="r", branch="main", token="t") is None
    )


@pytest.mark.asyncio
async def test_list_commit_statuses_keeps_latest_context(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(
        monkeypatch,
        {
            "statuses": [
                {"id": 2, "context": "ci", "state": "success"},
                {"id": 1, "context": "ci", "state": "failure"},
            ]
        },
    )

    statuses = await github_ci.list_commit_statuses(owner="o", repo="r", ref="s", token="t")

    assert statuses == [{"id": 2, "context": "ci", "state": "success"}]


@pytest.mark.asyncio
async def test_has_repo_write_permission_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, {}, error=True)
    assert not await github_ci.has_repo_write_permission(
        owner="o", repo="r", username="bob", token="t"
    )
    assert not await github_ci.has_repo_write_permission(
        owner="o", repo="r", username="", token="t"
    )
