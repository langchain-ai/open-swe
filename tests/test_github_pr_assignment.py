from __future__ import annotations

import asyncio

import pytest

from agent.utils import authorship, github


class _FakeResponse:
    def __init__(self, status_code: int, payload: object) -> None:
        self.status_code = status_code
        self._payload = payload

    @property
    def is_success(self) -> bool:
        return 200 <= self.status_code < 300

    def json(self) -> object:
        return self._payload


class _FakeAsyncClient:
    def __init__(
        self, responses: list[_FakeResponse], calls: list[tuple[str, str, dict | None]]
    ) -> None:
        self._responses = responses
        self._calls = calls

    async def __aenter__(self) -> _FakeAsyncClient:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def post(
        self, url: str, *, headers: dict[str, str], json: dict | None = None
    ) -> _FakeResponse:
        self._calls.append(("POST", url, json))
        return self._responses.pop(0)

    async def get(
        self, url: str, *, headers: dict[str, str], params: dict[str, str | int]
    ) -> _FakeResponse:
        self._calls.append(("GET", url, params))
        return self._responses.pop(0)


class _RaiseOnSecondPostClient(_FakeAsyncClient):
    async def post(
        self, url: str, *, headers: dict[str, str], json: dict | None = None
    ) -> _FakeResponse:
        self._calls.append(("POST", url, json))
        if len(self._calls) == 2:
            request = github.httpx.Request("POST", url)
            raise github.httpx.ConnectError("boom", request=request)
        return self._responses.pop(0)


def test_create_github_pr_assigns_created_pr(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str, dict | None]] = []
    responses = [
        _FakeResponse(201, {"html_url": "https://github.com/o/r/pull/12", "number": 12}),
        _FakeResponse(201, {"assignees": [{"login": "octocat"}]}),
    ]
    monkeypatch.setattr(
        github.httpx,
        "AsyncClient",
        lambda: _FakeAsyncClient(responses, calls),
    )

    result = asyncio.run(
        github.create_github_pr(
            repo_owner="o",
            repo_name="r",
            github_token="token",
            title="feat: add assignment",
            head_branch="feature",
            base_branch="main",
            body="body",
            assignee_login="octocat",
        )
    )

    assert result == ("https://github.com/o/r/pull/12", 12, False)
    assert calls == [
        (
            "POST",
            "https://api.github.com/repos/o/r/pulls",
            {
                "title": "feat: add assignment",
                "head": "feature",
                "base": "main",
                "body": "body",
                "draft": True,
            },
        ),
        (
            "POST",
            "https://api.github.com/repos/o/r/issues/12/assignees",
            {"assignees": ["octocat"]},
        ),
    ]


def test_create_github_pr_assigns_existing_pr(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str, dict | None]] = []
    responses = [
        _FakeResponse(422, {"message": "A pull request already exists"}),
        _FakeResponse(
            200,
            [{"html_url": "https://github.com/o/r/pull/7", "number": 7}],
        ),
        _FakeResponse(201, {"assignees": [{"login": "octocat"}]}),
    ]
    monkeypatch.setattr(
        github.httpx,
        "AsyncClient",
        lambda: _FakeAsyncClient(responses, calls),
    )

    result = asyncio.run(
        github.create_github_pr(
            repo_owner="o",
            repo_name="r",
            github_token="token",
            title="feat: add assignment",
            head_branch="feature",
            base_branch="main",
            body="body",
            assignee_login="octocat",
        )
    )

    assert result == ("https://github.com/o/r/pull/7", 7, True)
    assert calls == [
        (
            "POST",
            "https://api.github.com/repos/o/r/pulls",
            {
                "title": "feat: add assignment",
                "head": "feature",
                "base": "main",
                "body": "body",
                "draft": True,
            },
        ),
        (
            "GET",
            "https://api.github.com/repos/o/r/pulls",
            {"head": "o:feature", "state": "open", "per_page": 1},
        ),
        (
            "POST",
            "https://api.github.com/repos/o/r/issues/7/assignees",
            {"assignees": ["octocat"]},
        ),
    ]


def test_create_github_pr_keeps_success_when_assignment_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str, dict | None]] = []
    responses = [_FakeResponse(201, {"html_url": "https://github.com/o/r/pull/12", "number": 12})]
    monkeypatch.setattr(
        github.httpx,
        "AsyncClient",
        lambda: _RaiseOnSecondPostClient(responses, calls),
    )

    result = asyncio.run(
        github.create_github_pr(
            repo_owner="o",
            repo_name="r",
            github_token="token",
            title="feat: add assignment",
            head_branch="feature",
            base_branch="main",
            body="body",
            assignee_login="octocat",
        )
    )

    assert result == ("https://github.com/o/r/pull/12", 12, False)
    assert calls == [
        (
            "POST",
            "https://api.github.com/repos/o/r/pulls",
            {
                "title": "feat: add assignment",
                "head": "feature",
                "base": "main",
                "body": "body",
                "draft": True,
            },
        ),
        (
            "POST",
            "https://api.github.com/repos/o/r/issues/12/assignees",
            {"assignees": ["octocat"]},
        ),
    ]


def test_resolve_triggering_user_identity_keeps_github_login() -> None:
    identity = authorship.resolve_triggering_user_identity(
        {"configurable": {"github_login": "octocat", "github_user_id": 12345}}
    )

    assert identity is not None
    assert identity.github_login == "octocat"
    assert identity.commit_email == "12345+octocat@users.noreply.github.com"
