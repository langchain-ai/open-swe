from __future__ import annotations

import asyncio
from typing import Any

import pytest

from agent.utils import github


class _FakeResponse:
    def __init__(self, status_code: int, payload: Any) -> None:
        self.status_code = status_code
        self._payload = payload

    @property
    def is_success(self) -> bool:
        return 200 <= self.status_code < 300

    def json(self) -> Any:
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

    async def patch(
        self, url: str, *, headers: dict[str, str], json: dict | None = None
    ) -> _FakeResponse:
        self._calls.append(("PATCH", url, json))
        return self._responses.pop(0)


class _RaiseOnLabelPostClient(_FakeAsyncClient):
    """Raises on the second POST (the label call) to simulate network failure."""

    async def post(
        self, url: str, *, headers: dict[str, str], json: dict | None = None
    ) -> _FakeResponse:
        self._calls.append(("POST", url, json))
        if len(self._calls) == 2:
            request = github.httpx.Request("POST", url)
            raise github.httpx.ConnectError("boom", request=request)
        return self._responses.pop(0)


class _AlwaysRaisePostClient(_FakeAsyncClient):
    """Raises on every POST to simulate network failure."""

    async def post(
        self, url: str, *, headers: dict[str, str], json: dict | None = None
    ) -> _FakeResponse:
        self._calls.append(("POST", url, json))
        request = github.httpx.Request("POST", url)
        raise github.httpx.ConnectError("boom", request=request)


# -- _add_label tests --


def test_add_label_success(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str, dict | None]] = []
    responses = [_FakeResponse(200, [{"name": "OpenSWE"}])]
    client = _FakeAsyncClient(responses, calls)

    asyncio.run(github._add_label(client, "o", "r", "token", 12))

    assert calls == [
        ("POST", "https://api.github.com/repos/o/r/issues/12/labels", {"labels": ["OpenSWE"]}),
    ]


def test_add_label_skips_when_no_pr_number() -> None:
    """Should return immediately without making any API calls."""

    async def _run() -> None:
        # Pass a mock that would fail if called
        await github._add_label(None, "o", "r", "token", None)  # type: ignore[arg-type]

    asyncio.run(_run())


def test_add_label_does_not_raise_on_api_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str, dict | None]] = []
    responses = [_FakeResponse(403, {"message": "Resource not accessible by integration"})]
    client = _FakeAsyncClient(responses, calls)

    # Should not raise
    asyncio.run(github._add_label(client, "o", "r", "token", 12))

    assert len(calls) == 1


def test_add_label_does_not_raise_on_http_error() -> None:
    calls: list[tuple[str, str, dict | None]] = []
    responses: list[_FakeResponse] = []
    client = _AlwaysRaisePostClient(responses, calls)

    # The POST will raise — should not propagate
    asyncio.run(github._add_label(client, "o", "r", "token", 5))

    assert len(calls) == 1


# -- create_github_pr with label tests --


def test_create_pr_adds_label_on_new_pr(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str, dict | None]] = []
    responses = [
        _FakeResponse(201, {"html_url": "https://github.com/o/r/pull/12", "number": 12}),
        _FakeResponse(200, [{"name": "OpenSWE"}]),
    ]
    monkeypatch.setattr(github.httpx, "AsyncClient", lambda: _FakeAsyncClient(responses, calls))

    result = asyncio.run(
        github.create_github_pr(
            repo_owner="o",
            repo_name="r",
            github_token="user-token",
            title="feat: test",
            head_branch="feature",
            base_branch="main",
            body="body",
            installation_token="install-token",
        )
    )

    assert result == ("https://github.com/o/r/pull/12", 12, False)
    # First call: create PR with user token, second call: add label with install token
    assert calls[0] == (
        "POST",
        "https://api.github.com/repos/o/r/pulls",
        {"title": "feat: test", "head": "feature", "base": "main", "body": "body", "draft": True},
    )
    assert calls[1] == (
        "POST",
        "https://api.github.com/repos/o/r/issues/12/labels",
        {"labels": ["OpenSWE"]},
    )


def test_create_pr_adds_label_on_existing_pr(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str, dict | None]] = []
    responses = [
        _FakeResponse(422, {"message": "A pull request already exists"}),
        _FakeResponse(200, [{"html_url": "https://github.com/o/r/pull/7", "number": 7}]),
        _FakeResponse(200, [{"name": "OpenSWE"}]),
    ]
    monkeypatch.setattr(github.httpx, "AsyncClient", lambda: _FakeAsyncClient(responses, calls))

    result = asyncio.run(
        github.create_github_pr(
            repo_owner="o",
            repo_name="r",
            github_token="user-token",
            title="feat: test",
            head_branch="feature",
            base_branch="main",
            body="body",
            installation_token="install-token",
        )
    )

    assert result == ("https://github.com/o/r/pull/7", 7, True)
    assert calls[2] == (
        "POST",
        "https://api.github.com/repos/o/r/issues/7/labels",
        {"labels": ["OpenSWE"]},
    )
    assert [call[0] for call in calls] == ["POST", "GET", "POST"]


def test_create_pr_returns_existing_pr_when_existing_pr_label_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str, dict | None]] = []
    responses = [
        _FakeResponse(422, {"message": "A pull request already exists"}),
        _FakeResponse(200, [{"html_url": "https://github.com/o/r/pull/7", "number": 7}]),
        _FakeResponse(403, {"message": "Resource not accessible by integration"}),
    ]
    monkeypatch.setattr(github.httpx, "AsyncClient", lambda: _FakeAsyncClient(responses, calls))

    result = asyncio.run(
        github.create_github_pr(
            repo_owner="o",
            repo_name="r",
            github_token="token",
            title="feat: test",
            head_branch="feature",
            base_branch="main",
            body="body",
        )
    )

    assert result == ("https://github.com/o/r/pull/7", 7, True)
    assert calls == [
        (
            "POST",
            "https://api.github.com/repos/o/r/pulls",
            {
                "title": "feat: test",
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
            "https://api.github.com/repos/o/r/issues/7/labels",
            {"labels": ["OpenSWE"]},
        ),
    ]


def test_create_pr_preserves_existing_pr_metadata_without_token_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str, dict | None]] = []
    responses = [
        _FakeResponse(422, {"message": "A pull request already exists"}),
        _FakeResponse(200, [{"html_url": "https://github.com/o/r/pull/7", "number": 7}]),
        _FakeResponse(200, [{"name": "OpenSWE"}]),
    ]
    monkeypatch.setattr(github.httpx, "AsyncClient", lambda: _FakeAsyncClient(responses, calls))

    result = asyncio.run(
        github.create_github_pr(
            repo_owner="o",
            repo_name="r",
            github_token="user-token",
            title="feat: test",
            head_branch="feature",
            base_branch="main",
            body="body",
            installation_token="install-token",
        )
    )

    assert result == ("https://github.com/o/r/pull/7", 7, True)
    assert [call[0] for call in calls] == ["POST", "GET", "POST"]


def test_create_pr_succeeds_when_label_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """PR creation should succeed even if labeling fails."""
    calls: list[tuple[str, str, dict | None]] = []
    responses = [
        _FakeResponse(201, {"html_url": "https://github.com/o/r/pull/12", "number": 12}),
    ]
    monkeypatch.setattr(
        github.httpx, "AsyncClient", lambda: _RaiseOnLabelPostClient(responses, calls)
    )

    result = asyncio.run(
        github.create_github_pr(
            repo_owner="o",
            repo_name="r",
            github_token="token",
            title="feat: test",
            head_branch="feature",
            base_branch="main",
            body="body",
        )
    )

    assert result == ("https://github.com/o/r/pull/12", 12, False)


def test_create_pr_uses_github_token_for_label_when_no_installation_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When installation_token is not provided, github_token is used for labeling."""
    calls: list[tuple[str, str, dict | None]] = []
    responses = [
        _FakeResponse(201, {"html_url": "https://github.com/o/r/pull/12", "number": 12}),
        _FakeResponse(200, [{"name": "OpenSWE"}]),
    ]
    monkeypatch.setattr(github.httpx, "AsyncClient", lambda: _FakeAsyncClient(responses, calls))

    result = asyncio.run(
        github.create_github_pr(
            repo_owner="o",
            repo_name="r",
            github_token="token",
            title="feat: test",
            head_branch="feature",
            base_branch="main",
            body="body",
        )
    )

    assert result == ("https://github.com/o/r/pull/12", 12, False)
    # Both calls made — label uses the same token
    assert len(calls) == 2


def test_create_pr_falls_back_to_installation_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When user token fails, retries with installation token."""
    calls: list[tuple[str, str, dict | None]] = []
    responses = [
        # First attempt with user token → 403
        _FakeResponse(403, {"message": "Resource not accessible by integration"}),
        # Second attempt with installation token → 201
        _FakeResponse(201, {"html_url": "https://github.com/o/r/pull/12", "number": 12}),
        # Label
        _FakeResponse(200, [{"name": "OpenSWE"}]),
    ]
    monkeypatch.setattr(github.httpx, "AsyncClient", lambda: _FakeAsyncClient(responses, calls))

    result = asyncio.run(
        github.create_github_pr(
            repo_owner="o",
            repo_name="r",
            github_token="user-token",
            title="feat: test",
            head_branch="feature",
            base_branch="main",
            body="body",
            installation_token="install-token",
        )
    )

    assert result == ("https://github.com/o/r/pull/12", 12, False)
    # 3 calls: failed PR create, successful PR create, label
    assert len(calls) == 3


def test_create_pr_falls_back_on_http_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When user token raises HTTPError, retries with installation token."""
    calls: list[tuple[str, str, dict | None]] = []
    responses = [
        # Installation token succeeds
        _FakeResponse(201, {"html_url": "https://github.com/o/r/pull/12", "number": 12}),
        # Label
        _FakeResponse(200, [{"name": "OpenSWE"}]),
    ]

    class _RaiseFirstPostClient(_FakeAsyncClient):
        """Raises on the first POST only (user token), then delegates to normal behavior."""

        _first = True

        async def post(
            self, url: str, *, headers: dict[str, str], json: dict | None = None
        ) -> _FakeResponse:
            self._calls.append(("POST", url, json))
            if self._first:
                self._first = False
                request = github.httpx.Request("POST", url)
                raise github.httpx.ConnectError("boom", request=request)
            return self._responses.pop(0)

    monkeypatch.setattr(
        github.httpx, "AsyncClient", lambda: _RaiseFirstPostClient(responses, calls)
    )

    result = asyncio.run(
        github.create_github_pr(
            repo_owner="o",
            repo_name="r",
            github_token="user-token",
            title="feat: test",
            head_branch="feature",
            base_branch="main",
            body="body",
            installation_token="install-token",
        )
    )

    assert result == ("https://github.com/o/r/pull/12", 12, False)
    # 3 calls: failed POST (user token), successful POST (install token), label POST
    assert len(calls) == 3


def test_create_pr_no_fallback_when_tokens_are_same(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When github_token == installation_token, no retry happens."""
    calls: list[tuple[str, str, dict | None]] = []
    responses = [
        _FakeResponse(403, {"message": "Resource not accessible by integration"}),
    ]
    monkeypatch.setattr(github.httpx, "AsyncClient", lambda: _FakeAsyncClient(responses, calls))

    result = asyncio.run(
        github.create_github_pr(
            repo_owner="o",
            repo_name="r",
            github_token="same-token",
            title="feat: test",
            head_branch="feature",
            base_branch="main",
            body="body",
            installation_token="same-token",
        )
    )

    assert result == (None, None, False)
    # Only 1 call — no retry since tokens are identical
    assert len(calls) == 1
