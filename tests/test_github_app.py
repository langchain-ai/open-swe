from __future__ import annotations

from typing import Any

import pytest

from agent.utils import github_app


class _FakeResponse:
    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict[str, str]:
        return {"token": "token", "expires_at": "expires"}


class _FakeAsyncClient:
    last_post: dict[str, Any] | None = None

    async def __aenter__(self) -> _FakeAsyncClient:
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        return None

    async def post(self, url: str, **kwargs: Any) -> _FakeResponse:
        type(self).last_post = {"url": url, **kwargs}
        return _FakeResponse()


@pytest.mark.asyncio
async def test_installation_token_can_be_scoped_to_repository_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(github_app, "GITHUB_APP_ID", "1")
    monkeypatch.setattr(github_app, "GITHUB_APP_PRIVATE_KEY", "key")
    monkeypatch.setattr(github_app, "GITHUB_APP_INSTALLATION_ID", "2")
    monkeypatch.setattr(github_app, "_generate_app_jwt", lambda: "jwt")
    monkeypatch.setattr(github_app.httpx, "AsyncClient", _FakeAsyncClient)

    token, expires_at = await github_app.get_github_app_installation_token_with_expiry(
        repository_ids=[123]
    )

    assert token == "token"
    assert expires_at == "expires"
    assert _FakeAsyncClient.last_post is not None
    assert _FakeAsyncClient.last_post["json"] == {"repository_ids": [123]}


@pytest.mark.asyncio
async def test_installation_token_omits_scope_for_full_installation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(github_app, "GITHUB_APP_ID", "1")
    monkeypatch.setattr(github_app, "GITHUB_APP_PRIVATE_KEY", "key")
    monkeypatch.setattr(github_app, "GITHUB_APP_INSTALLATION_ID", "2")
    monkeypatch.setattr(github_app, "_generate_app_jwt", lambda: "jwt")
    monkeypatch.setattr(github_app.httpx, "AsyncClient", _FakeAsyncClient)

    await github_app.get_github_app_installation_token_with_expiry()

    assert _FakeAsyncClient.last_post is not None
    assert _FakeAsyncClient.last_post["json"] is None
