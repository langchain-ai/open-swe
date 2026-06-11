from __future__ import annotations

import asyncio
import sys
from typing import Any

import pytest

import agent.tools.open_pull_request  # noqa: F401

opr = sys.modules["agent.tools.open_pull_request"]


class _FakeResponse:
    def __init__(self, status_code: int, payload: Any = None, text: str = "") -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self) -> Any:
        return self._payload


class _FakeClient:
    def __init__(self, *, post: _FakeResponse, get: _FakeResponse | None = None) -> None:
        self._post = post
        self._get = get
        self.post_calls: list[dict[str, Any]] = []
        self.get_calls: list[dict[str, Any]] = []

    async def __aenter__(self) -> _FakeClient:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None

    async def post(
        self, url: str, *, headers: dict[str, str], json: dict[str, Any]
    ) -> _FakeResponse:
        self.post_calls.append({"url": url, "headers": headers, "json": json})
        return self._post

    async def get(
        self, url: str, *, headers: dict[str, str], params: dict[str, str]
    ) -> _FakeResponse:
        self.get_calls.append({"url": url, "headers": headers, "params": params})
        assert self._get is not None
        return self._get


def _install_client(monkeypatch: pytest.MonkeyPatch, client: _FakeClient) -> None:
    monkeypatch.setattr(opr.httpx, "AsyncClient", lambda **_kwargs: client)


def _set_config(monkeypatch: pytest.MonkeyPatch, configurable: dict[str, Any]) -> None:
    monkeypatch.setattr(opr, "get_config", lambda: {"configurable": configurable})


def _open() -> dict[str, Any]:
    return asyncio.run(
        opr._open_pull_request(
            owner="langchain-ai",
            repo="open-swe",
            head="open-swe/feature",
            base="main",
            title="feat: x",
            body="body",
            draft=True,
        )
    )


def test_uses_user_token_for_slack_with_login(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_config(monkeypatch, {"source": "slack", "github_login": "johannes117"})

    from agent.dashboard import profiles

    async def fake_user_token(login: str, **_kw: Any) -> str | None:
        assert login == "johannes117"
        return "user-tok"

    monkeypatch.setattr(profiles, "get_valid_access_token", fake_user_token)

    async def fail_bot() -> str | None:
        raise AssertionError("bot token should not be used when a user token exists")

    monkeypatch.setattr(opr, "get_github_app_installation_token", fail_bot)

    client = _FakeClient(
        post=_FakeResponse(
            201,
            {"html_url": "https://x/pull/1", "number": 1, "user": {"login": "johannes117"}},
        )
    )
    _install_client(monkeypatch, client)

    result = _open()

    assert result["success"] is True
    assert result["created"] is True
    assert result["url"] == "https://x/pull/1"
    assert result["author"] == "johannes117"
    assert result["token_kind"] == "user"
    assert client.post_calls[0]["headers"]["Authorization"] == "Bearer user-tok"
    assert client.post_calls[0]["json"] == {
        "title": "feat: x",
        "head": "open-swe/feature",
        "base": "main",
        "body": "body",
        "draft": True,
    }


def test_falls_back_to_bot_for_github_source(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_config(monkeypatch, {"source": "github", "github_login": "johannes117"})

    from agent.dashboard import profiles

    async def fail_user_token(login: str, **_kw: Any) -> str | None:
        raise AssertionError("user token should not be resolved for github source")

    monkeypatch.setattr(profiles, "get_valid_access_token", fail_user_token)

    async def fake_bot() -> str | None:
        return "bot-tok"

    monkeypatch.setattr(opr, "get_github_app_installation_token", fake_bot)

    client = _FakeClient(
        post=_FakeResponse(
            201, {"html_url": "https://x/pull/2", "number": 2, "user": {"login": "open-swe[bot]"}}
        )
    )
    _install_client(monkeypatch, client)

    result = _open()

    assert result["token_kind"] == "bot"
    assert client.post_calls[0]["headers"]["Authorization"] == "Bearer bot-tok"


def test_falls_back_to_bot_when_user_token_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_config(monkeypatch, {"source": "slack", "github_login": "johannes117"})

    from agent.dashboard import profiles

    async def no_user_token(login: str, **_kw: Any) -> str | None:
        return None

    monkeypatch.setattr(profiles, "get_valid_access_token", no_user_token)

    async def fake_bot() -> str | None:
        return "bot-tok"

    monkeypatch.setattr(opr, "get_github_app_installation_token", fake_bot)

    client = _FakeClient(post=_FakeResponse(201, {"html_url": "u", "number": 3, "user": {}}))
    _install_client(monkeypatch, client)

    assert _open()["token_kind"] == "bot"


def test_returns_existing_pr_on_422(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_config(monkeypatch, {"source": "slack", "github_login": "johannes117"})

    from agent.dashboard import profiles

    monkeypatch.setattr(profiles, "get_valid_access_token", lambda *_a, **_k: _coro("user-tok"))
    monkeypatch.setattr(opr, "get_github_app_installation_token", lambda: _coro("bot"))

    client = _FakeClient(
        post=_FakeResponse(422, text="A pull request already exists"),
        get=_FakeResponse(
            200, [{"html_url": "https://x/pull/9", "number": 9, "user": {"login": "johannes117"}}]
        ),
    )
    _install_client(monkeypatch, client)

    result = _open()

    assert result["success"] is True
    assert result["created"] is False
    assert result["number"] == 9
    assert client.get_calls[0]["params"] == {
        "head": "langchain-ai:open-swe/feature",
        "state": "open",
    }


def test_error_surfaced_on_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_config(monkeypatch, {"source": "slack", "github_login": "johannes117"})

    from agent.dashboard import profiles

    monkeypatch.setattr(profiles, "get_valid_access_token", lambda *_a, **_k: _coro("user-tok"))
    monkeypatch.setattr(opr, "get_github_app_installation_token", lambda: _coro("bot"))

    client = _FakeClient(post=_FakeResponse(403, text="Resource not accessible"))
    _install_client(monkeypatch, client)

    result = _open()

    assert result["success"] is False
    assert "403" in result["error"]


async def _coro(value: Any) -> Any:
    return value


def test_derive_pr_state_prefers_merged() -> None:
    assert opr.derive_pr_state(state="closed", merged=True, draft=True) == "merged"


def test_derive_pr_state_closed_over_draft() -> None:
    assert opr.derive_pr_state(state="closed", merged=False, draft=True) == "closed"


def test_derive_pr_state_draft() -> None:
    assert opr.derive_pr_state(state="open", merged=False, draft=True) == "draft"


def test_derive_pr_state_open() -> None:
    assert opr.derive_pr_state(state="open", merged=False, draft=False) == "open"
