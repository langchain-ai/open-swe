"""GitHub App JWT issuer selection and installation auto-discovery."""

from typing import Any

import pytest

from agent.github import app as github_app


@pytest.fixture(autouse=True)
def _reset(monkeypatch: pytest.MonkeyPatch) -> Any:
    github_app.clear_app_token_cache()
    monkeypatch.setattr(github_app, "GITHUB_APP_ID", "")
    monkeypatch.setattr(github_app, "GITHUB_APP_CLIENT_ID", "Iv1.client")
    monkeypatch.setattr(github_app, "GITHUB_APP_PRIVATE_KEY", "key")
    monkeypatch.setattr(github_app, "GITHUB_APP_INSTALLATION_ID", "")
    monkeypatch.setattr(github_app, "_generate_app_jwt", lambda: "jwt")
    yield
    github_app.clear_app_token_cache()


class _Response:
    def __init__(self, payload: Any, status: int = 200) -> None:
        self._payload = payload
        self.status_code = status

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"status {self.status_code}")

    def json(self) -> Any:
        return self._payload


def _client_factory(routes: dict[str, Any]) -> type:
    class Client:
        gets: list[str] = []
        posts: list[str] = []

        def __init__(self, **kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> Client:
            return self

        async def __aexit__(self, *exc: object) -> None:
            return None

        async def get(self, url: str, **kwargs: Any) -> _Response:
            type(self).gets.append(url)
            path = url.split("api.github.com", 1)[1].split("?", 1)[0]
            payload = routes.get(path)
            if payload is None:
                return _Response({"message": "Not Found"}, 404)
            return _Response(payload)

        async def post(self, url: str, **kwargs: Any) -> _Response:
            type(self).posts.append(url)
            return _Response({"token": "tok", "expires_at": "2099-01-01T00:00:00Z"})

    return Client


def test_issuer_prefers_client_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(github_app, "GITHUB_APP_ID", "12345")
    assert github_app._app_jwt_issuer() == "Iv1.client"


def test_issuer_falls_back_to_app_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(github_app, "GITHUB_APP_CLIENT_ID", "")
    monkeypatch.setattr(github_app, "GITHUB_APP_ID", "12345")
    assert github_app._app_jwt_issuer() == "12345"
    assert github_app._app_credentials_configured()


def test_not_configured_without_issuer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(github_app, "GITHUB_APP_CLIENT_ID", "")
    assert not github_app._app_credentials_configured()


def test_not_configured_without_private_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(github_app, "GITHUB_APP_PRIVATE_KEY", "")
    assert not github_app._app_credentials_configured()


async def test_env_installation_id_short_circuits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(github_app, "GITHUB_APP_INSTALLATION_ID", "77")
    client = _client_factory({})
    monkeypatch.setattr(github_app.httpx, "AsyncClient", client)

    assert await github_app.resolve_default_installation_id() == "77"
    assert client.gets == []


async def test_single_installation_is_used_and_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client_factory(
        {"/app/installations": [{"id": 42, "account": {"login": "acme", "type": "Organization"}}]}
    )
    monkeypatch.setattr(github_app.httpx, "AsyncClient", client)

    assert await github_app.resolve_default_installation_id() == "42"
    assert await github_app.resolve_default_installation_id() == "42"
    assert client.gets == ["https://api.github.com/app/installations?per_page=100&page=1"]


async def test_single_installation_mints_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client_factory({"/app/installations": [{"id": 42, "account": {"login": "acme"}}]})
    monkeypatch.setattr(github_app.httpx, "AsyncClient", client)

    token, expires_at = await github_app.get_github_app_installation_token_with_expiry()

    assert (token, expires_at) == ("tok", "2099-01-01T00:00:00Z")
    assert client.posts == ["https://api.github.com/app/installations/42/access_tokens"]


async def test_multiple_installations_do_not_auto_select(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client_factory(
        {
            "/app/installations": [
                {"id": 1, "account": {"login": "a"}},
                {"id": 2, "account": {"login": "b"}},
            ]
        }
    )
    monkeypatch.setattr(github_app.httpx, "AsyncClient", client)

    assert await github_app.resolve_default_installation_id() is None
    token, _ = await github_app.get_github_app_installation_token_with_expiry()
    assert token is None
    assert client.posts == []


async def test_zero_installations_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client_factory({"/app/installations": []})
    monkeypatch.setattr(github_app.httpx, "AsyncClient", client)

    assert await github_app.resolve_default_installation_id() is None


async def test_failed_discovery_is_throttled(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client_factory({})
    monkeypatch.setattr(github_app.httpx, "AsyncClient", client)

    assert await github_app.resolve_default_installation_id() is None
    assert await github_app.resolve_default_installation_id() is None
    assert len(client.gets) == 1


async def test_installations_paginate(monkeypatch: pytest.MonkeyPatch) -> None:
    first_page = [{"id": i, "account": {"login": f"o{i}"}} for i in range(1, 101)]
    gets: list[str] = []

    class Client:
        def __init__(self, **kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> "Client":
            return self

        async def __aexit__(self, *exc: object) -> None:
            return None

        async def get(self, url: str, **kwargs: Any) -> _Response:
            gets.append(url)
            if url.endswith("page=1"):
                return _Response(first_page)
            return _Response([{"id": 101, "account": {"login": "o101"}}])

    monkeypatch.setattr(github_app.httpx, "AsyncClient", Client)

    installations = await github_app.list_app_installations()

    assert len(installations) == 101
    assert len(gets) == 2


async def test_repo_context_wins_over_single_installation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client_factory(
        {
            "/app/installations": [{"id": 1, "account": {"login": "a"}}],
            "/repos/acme/widgets/installation": {"id": 9},
        }
    )
    monkeypatch.setattr(github_app.httpx, "AsyncClient", client)

    assert await github_app.resolve_default_installation_id(owner="acme", repo="widgets") == "9"
    token, _ = await github_app.get_github_app_installation_token_with_expiry(
        owner="acme", repo="widgets"
    )
    assert token == "tok"
    assert client.posts == ["https://api.github.com/app/installations/9/access_tokens"]


async def test_repo_context_is_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client_factory({"/repos/acme/widgets/installation": {"id": 9}})
    monkeypatch.setattr(github_app.httpx, "AsyncClient", client)

    await github_app.resolve_default_installation_id(owner="acme", repo="widgets")
    await github_app.resolve_default_installation_id(owner="Acme", repo="Widgets")

    assert len(client.gets) == 1


async def test_org_context_without_repo(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client_factory({"/orgs/acme/installation": {"id": 11}})
    monkeypatch.setattr(github_app.httpx, "AsyncClient", client)

    assert await github_app.resolve_default_installation_id(owner="acme") == "11"


async def test_unknown_repo_falls_back_to_single_installation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client_factory({"/app/installations": [{"id": 1, "account": {"login": "a"}}]})
    monkeypatch.setattr(github_app.httpx, "AsyncClient", client)

    assert await github_app.resolve_default_installation_id(owner="acme", repo="missing") == "1"


async def test_env_override_beats_repo_context(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(github_app, "GITHUB_APP_INSTALLATION_ID", "5")
    client = _client_factory({"/repos/acme/widgets/installation": {"id": 9}})
    monkeypatch.setattr(github_app.httpx, "AsyncClient", client)

    assert await github_app.resolve_default_installation_id(owner="acme", repo="widgets") == "5"
    assert client.gets == []


async def test_unconfigured_app_skips_discovery(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(github_app, "GITHUB_APP_CLIENT_ID", "")
    client = _client_factory({"/app/installations": [{"id": 1}]})
    monkeypatch.setattr(github_app.httpx, "AsyncClient", client)

    assert await github_app.resolve_default_installation_id() is None
    assert client.gets == []
