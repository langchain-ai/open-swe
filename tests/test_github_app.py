from __future__ import annotations

import asyncio

import pytest

from agent.utils import github_app


def test_get_github_app_config_reads_current_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_APP_ID", "runtime-app-id")
    monkeypatch.setenv("GITHUB_APP_PRIVATE_KEY", "runtime-private-key")
    monkeypatch.setenv("GITHUB_APP_INSTALLATION_ID", "runtime-installation-id")

    config = github_app._get_github_app_config()

    assert config.app_id == "runtime-app-id"
    assert config.private_key == "runtime-private-key"
    assert config.installation_id == "runtime-installation-id"


def test_get_github_app_installation_token_uses_runtime_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, str] = {}

    monkeypatch.setenv("GITHUB_APP_ID", "runtime-app-id")
    monkeypatch.setenv("GITHUB_APP_PRIVATE_KEY", "line-1\\nline-2")
    monkeypatch.setenv("GITHUB_APP_INSTALLATION_ID", "runtime-installation-id")

    def fake_encode(payload: dict[str, int | str], private_key: str, algorithm: str) -> str:
        seen["iss"] = str(payload["iss"])
        seen["private_key"] = private_key
        seen["algorithm"] = algorithm
        return "app-jwt"

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, str]:
            return {"token": "installation-token"}

    class FakeAsyncClient:
        async def __aenter__(self) -> FakeAsyncClient:
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def post(self, url: str, headers: dict[str, str]) -> FakeResponse:
            seen["url"] = url
            seen["authorization"] = headers["Authorization"]
            return FakeResponse()

    monkeypatch.setattr(github_app.jwt, "encode", fake_encode)
    monkeypatch.setattr(github_app.httpx, "AsyncClient", FakeAsyncClient)

    token = asyncio.run(github_app.get_github_app_installation_token())

    assert token == "installation-token"
    assert seen == {
        "iss": "runtime-app-id",
        "private_key": "line-1\nline-2",
        "algorithm": "RS256",
        "url": "https://api.github.com/app/installations/runtime-installation-id/access_tokens",
        "authorization": "Bearer app-jwt",
    }


def test_get_github_app_installation_token_returns_none_without_full_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GITHUB_APP_ID", raising=False)
    monkeypatch.delenv("GITHUB_APP_PRIVATE_KEY", raising=False)
    monkeypatch.delenv("GITHUB_APP_INSTALLATION_ID", raising=False)

    assert asyncio.run(github_app.get_github_app_installation_token()) is None
