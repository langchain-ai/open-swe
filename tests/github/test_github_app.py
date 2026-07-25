from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from agent.utils import github_app


@pytest.fixture(autouse=True)
def _clear_token_cache() -> Any:
    github_app.clear_app_token_cache()
    yield
    github_app.clear_app_token_cache()


class _FakeResponse:
    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict[str, str]:
        return {"token": "token", "expires_at": "expires"}


class _FakeAsyncClient:
    last_post: dict[str, Any] | None = None

    def __init__(self, **kwargs: Any) -> None:
        pass

    async def __aenter__(self) -> _FakeAsyncClient:
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        return None

    async def post(self, url: str, **kwargs: Any) -> _FakeResponse:
        type(self).last_post = {"url": url, **kwargs}
        return _FakeResponse()


def _configure(monkeypatch: pytest.MonkeyPatch, client_cls: type) -> None:
    monkeypatch.setattr(github_app, "GITHUB_APP_ID", "1")
    monkeypatch.setattr(github_app, "GITHUB_APP_PRIVATE_KEY", "key")
    monkeypatch.setattr(github_app, "GITHUB_APP_INSTALLATION_ID", "2")
    monkeypatch.setattr(github_app, "_generate_app_jwt", lambda: "jwt")
    monkeypatch.setattr(github_app.httpx, "AsyncClient", client_cls)


class _CountingResponse:
    def __init__(self, expires_at: str) -> None:
        self._expires_at = expires_at

    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict[str, str]:
        return {"token": "tok-123", "expires_at": self._expires_at}


class _CountingClient:
    posts = 0
    expires_at = "2099-01-01T00:00:00Z"

    def __init__(self, **kwargs: Any) -> None:
        pass

    async def __aenter__(self) -> _CountingClient:
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        return None

    async def post(self, url: str, **kwargs: Any) -> _CountingResponse:
        type(self).posts += 1
        return _CountingResponse(type(self).expires_at)


@pytest.mark.asyncio
async def test_token_is_cached_until_near_expiry(monkeypatch: pytest.MonkeyPatch) -> None:
    future = (datetime.now(UTC) + timedelta(hours=1)).isoformat()

    class Client(_CountingClient):
        posts = 0
        expires_at = future

    _configure(monkeypatch, Client)

    t1, _ = await github_app.get_github_app_installation_token_with_expiry()
    t2, _ = await github_app.get_github_app_installation_token_with_expiry()

    assert t1 == t2 == "tok-123"
    assert Client.posts == 1  # second call served from the in-process cache


@pytest.mark.asyncio
async def test_cache_is_scoped_per_repository_set(monkeypatch: pytest.MonkeyPatch) -> None:
    future = (datetime.now(UTC) + timedelta(hours=1)).isoformat()

    class Client(_CountingClient):
        posts = 0
        expires_at = future

    _configure(monkeypatch, Client)

    await github_app.get_github_app_installation_token_with_expiry(repositories=["a"])
    await github_app.get_github_app_installation_token_with_expiry(repositories=["b"])
    await github_app.get_github_app_installation_token_with_expiry(repositories=["a"])

    assert Client.posts == 2  # distinct scopes mint separately; the repeat is cached


@pytest.mark.asyncio
async def test_near_expiry_token_is_not_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    soon = (datetime.now(UTC) + timedelta(minutes=2)).isoformat()

    class Client(_CountingClient):
        posts = 0
        expires_at = soon

    _configure(monkeypatch, Client)

    await github_app.get_github_app_installation_token_with_expiry()
    await github_app.get_github_app_installation_token_with_expiry()

    assert Client.posts == 2  # within the safety margin -> re-minted every call


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
async def test_installation_token_includes_permissions(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(github_app, "GITHUB_APP_ID", "1")
    monkeypatch.setattr(github_app, "GITHUB_APP_PRIVATE_KEY", "key")
    monkeypatch.setattr(github_app, "GITHUB_APP_INSTALLATION_ID", "2")
    monkeypatch.setattr(github_app, "_generate_app_jwt", lambda: "jwt")
    monkeypatch.setattr(github_app.httpx, "AsyncClient", _FakeAsyncClient)

    await github_app.get_github_app_installation_token_with_expiry(
        repositories=["open-swe"], permissions={"workflows": "write", "contents": "write"}
    )

    assert _FakeAsyncClient.last_post is not None
    assert _FakeAsyncClient.last_post["json"] == {
        "repositories": ["open-swe"],
        "permissions": {"contents": "write", "workflows": "write"},
    }


@pytest.mark.asyncio
async def test_cache_is_scoped_per_permission_set(monkeypatch: pytest.MonkeyPatch) -> None:
    future = (datetime.now(UTC) + timedelta(hours=1)).isoformat()

    class Client(_CountingClient):
        posts = 0
        expires_at = future

    _configure(monkeypatch, Client)

    await github_app.get_github_app_installation_token_with_expiry(
        permissions={"contents": "write"}
    )
    await github_app.get_github_app_installation_token_with_expiry(
        permissions={"contents": "write", "workflows": "write"}
    )
    await github_app.get_github_app_installation_token_with_expiry(
        permissions={"contents": "write"}
    )

    assert Client.posts == 2


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


class _InstallationResponse:
    def __init__(self, data: dict[str, Any]) -> None:
        self._data = data

    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict[str, Any]:
        return self._data


class _InstallationClient:
    gets: list[str] = []
    posts: list[str] = []

    def __init__(self, **kwargs: Any) -> None:
        pass

    async def __aenter__(self) -> _InstallationClient:
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        return None

    async def get(self, url: str, **kwargs: Any) -> _InstallationResponse:
        type(self).gets.append(url)
        installation_id = "20" if "other/repo" in url else "10"
        return _InstallationResponse({"id": installation_id})

    async def post(self, url: str, **kwargs: Any) -> _InstallationResponse:
        type(self).posts.append(url)
        installation_id = url.split("/installations/", 1)[1].split("/", 1)[0]
        future = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
        return _InstallationResponse({"token": f"token-{installation_id}", "expires_at": future})


def _configure_installation_client(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(github_app, "GITHUB_APP_ID", "1")
    monkeypatch.setattr(github_app, "GITHUB_APP_PRIVATE_KEY", "key")
    monkeypatch.setattr(github_app, "GITHUB_APP_INSTALLATION_ID", "2")
    monkeypatch.setattr(github_app, "_generate_app_jwt", lambda: "jwt")
    monkeypatch.setattr(github_app.httpx, "AsyncClient", _InstallationClient)
    monkeypatch.delenv(github_app.GITHUB_APP_TARGET_REPO_ENV, raising=False)
    _InstallationClient.gets = []
    _InstallationClient.posts = []


@pytest.mark.asyncio
async def test_resolves_and_caches_installation_for_target_repo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_installation_client(monkeypatch)

    first = await github_app.get_github_app_installation_token(target_repo="owner/repo")
    second = await github_app.get_github_app_installation_token(target_repo="owner/repo")

    assert first == second == "token-10"
    assert _InstallationClient.gets == ["https://api.github.com/repos/owner/repo/installation"]
    assert _InstallationClient.posts == [
        "https://api.github.com/app/installations/10/access_tokens"
    ]


@pytest.mark.asyncio
async def test_installation_cache_expires(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_installation_client(monkeypatch)
    clock = iter([0.0, 301.0])
    monkeypatch.setattr(github_app, "_monotonic", lambda: next(clock))

    await github_app.get_github_app_installation_token(target_repo="owner/repo")
    await github_app.get_github_app_installation_token(target_repo="owner/repo")

    assert _InstallationClient.gets == [
        "https://api.github.com/repos/owner/repo/installation",
        "https://api.github.com/repos/owner/repo/installation",
    ]


@pytest.mark.asyncio
async def test_explicit_target_repo_precedes_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_installation_client(monkeypatch)
    monkeypatch.setenv(github_app.GITHUB_APP_TARGET_REPO_ENV, "other/repo")

    token = await github_app.get_github_app_installation_token(target_repo="owner/repo")

    assert token == "token-10"
    assert _InstallationClient.gets == ["https://api.github.com/repos/owner/repo/installation"]


@pytest.mark.asyncio
async def test_environment_target_repo_precedes_pinned_installation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_installation_client(monkeypatch)
    monkeypatch.setenv(github_app.GITHUB_APP_TARGET_REPO_ENV, "other/repo")

    token = await github_app.get_github_app_installation_token()

    assert token == "token-20"
    assert _InstallationClient.posts == [
        "https://api.github.com/app/installations/20/access_tokens"
    ]


@pytest.mark.asyncio
async def test_token_cache_isolated_by_installation(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_installation_client(monkeypatch)

    first = await github_app.get_github_app_installation_token(
        target_repo="owner/repo", repositories=["repo"]
    )
    second = await github_app.get_github_app_installation_token(
        target_repo="other/repo", repositories=["repo"]
    )

    assert first == "token-10"
    assert second == "token-20"
    assert len(_InstallationClient.posts) == 2


@pytest.mark.asyncio
async def test_blank_explicit_target_does_not_use_pinned_installation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_installation_client(monkeypatch)

    token = await github_app.get_github_app_installation_token(target_repo=" ", log_errors=False)

    assert token is None
    assert _InstallationClient.gets == []
    assert _InstallationClient.posts == []


@pytest.mark.asyncio
async def test_repo_lookup_failure_does_not_use_pinned_installation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailingClient(_InstallationClient):
        async def get(self, url: str, **kwargs: Any) -> _InstallationResponse:
            raise RuntimeError("lookup failed")

    _configure_installation_client(monkeypatch)
    monkeypatch.setattr(github_app.httpx, "AsyncClient", FailingClient)

    token = await github_app.get_github_app_installation_token(
        target_repo="owner/repo", log_errors=False
    )

    assert token is None
    assert FailingClient.posts == []
