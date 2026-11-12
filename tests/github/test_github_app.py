import asyncio
import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from functools import partial

import httpx2
import pytest
from githubkit import GitHub
from langgraph.runtime import Runtime

from openswe.agent_api.runs import in_graph_context
from openswe.github import app as github_app
from openswe.github import sdk
from tests.support.github_sdk import mock_github_sdk


@pytest.fixture(autouse=True)
def app_config(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(github_app, "GITHUB_APP_ID", "1")
    monkeypatch.setattr(github_app, "GITHUB_APP_PRIVATE_KEY", "test-key")
    monkeypatch.setattr(github_app, "GITHUB_APP_INSTALLATION_ID", "2")
    github_app.clear_app_token_cache()
    yield
    github_app.clear_app_token_cache()


@pytest.mark.parametrize("kind", ["org", "repo"])
async def test_resolves_installation_with_escaped_names(
    monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    def handle(request: httpx2.Request) -> httpx2.Response:
        expected = (
            "/orgs/secondary%2Forg/installation"
            if kind == "org"
            else "/repos/acme/private%2Frepo/installation"
        )
        assert request.url.raw_path.decode() == expected
        assert request.headers["authorization"] == "Bearer test-app-jwt"
        return httpx2.Response(200, json={"id": 3})

    mock_github_sdk(monkeypatch, handle)
    if kind == "org":
        result = await github_app.get_github_app_installation_id_for_org("secondary/org")
    else:
        result = await github_app.get_github_app_installation_id_for_repo("acme", "private/repo")
    assert result == 3


@pytest.mark.parametrize("status", [401, 403, 404, 500])
async def test_installation_lookup_fails_closed(
    monkeypatch: pytest.MonkeyPatch, status: int
) -> None:
    mock_github_sdk(
        monkeypatch, lambda request: httpx2.Response(status, json={"message": "unavailable"})
    )
    assert await github_app.get_github_app_installation_id_for_org("acme") is None
    assert await github_app.get_github_app_installation_id_for_repo("acme", "api") is None


@pytest.mark.parametrize(
    "minutes,min_lifetime,force_refresh,expected_requests",
    [
        (60, None, False, 1),
        (2, None, False, 2),
        (45, None, False, 1),
        (45, timedelta(minutes=50), False, 2),
        (60, None, True, 2),
    ],
)
async def test_token_cache_respects_expiry(
    monkeypatch: pytest.MonkeyPatch,
    minutes: int,
    min_lifetime: timedelta | None,
    force_refresh: bool,
    expected_requests: int,
) -> None:
    expires_at = (datetime.now(UTC) + timedelta(minutes=minutes)).isoformat()
    requests: list[httpx2.Request] = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(
            201, json={"token": f"token-{len(requests)}", "expires_at": expires_at}
        )

    mock_github_sdk(monkeypatch, handle)
    first, expiry = await github_app.get_github_app_installation_token_with_expiry()
    if min_lifetime is None:
        second, _ = await github_app.get_github_app_installation_token_with_expiry(
            force_refresh=force_refresh
        )
    else:
        second, _ = await github_app.get_github_app_installation_token_with_expiry(
            min_lifetime=min_lifetime
        )
    assert first == "token-1"
    assert second == f"token-{expected_requests}"
    assert expiry == expires_at
    assert len(requests) == expected_requests


async def test_cache_separates_repository_installation_and_permission_scopes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expires_at = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    requests: list[tuple[str, dict[str, object]]] = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        requests.append((request.url.path, json.loads(request.content)))
        return httpx2.Response(
            201, json={"token": f"token-{len(requests)}", "expires_at": expires_at}
        )

    mock_github_sdk(monkeypatch, handle)
    for _ in range(2):
        for installation_id in (2, 3):
            for ids in ([11], [22]):
                for permission in ("read", "write"):
                    token = await github_app.get_github_app_installation_token(
                        installation_id=installation_id,
                        repository_ids=ids,
                        permissions={"contents": permission},
                    )
                    assert token is not None
    assert len(requests) == 8
    assert len({(path, json.dumps(body, sort_keys=True)) for path, body in requests}) == 8
    refreshed, _ = await github_app.get_github_app_installation_token_with_expiry(
        installation_id=2,
        repository_ids=[11],
        permissions={"contents": "read"},
        force_refresh=True,
    )
    assert refreshed == "token-9"
    assert (
        await github_app.get_github_app_installation_token(
            installation_id=2, repository_ids=[11], permissions={"contents": "write"}
        )
        == "token-2"
    )
    assert len(requests) == 9


async def test_repository_names_and_full_installation_have_distinct_scopes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scopes: list[dict[str, object]] = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        scopes.append(json.loads(request.content))
        return httpx2.Response(
            201, json={"token": f"token-{len(scopes)}", "expires_at": "2099-01-01T00:00:00Z"}
        )

    mock_github_sdk(monkeypatch, handle)
    for names in (["a"], ["b"], ["a"], None):
        assert await github_app.get_github_app_installation_token(repositories=names)
    assert scopes == [{"repositories": ["a"]}, {"repositories": ["b"]}, {}]


async def test_token_request_preserves_repository_ids_and_permissions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handle(request: httpx2.Request) -> httpx2.Response:
        assert request.url.path == "/app/installations/3/access_tokens"
        assert json.loads(request.content) == {
            "repository_ids": [123],
            "permissions": {"contents": "write", "workflows": "write"},
        }
        return httpx2.Response(201, json={"token": "token", "expires_at": "2099-01-01T00:00:00Z"})

    mock_github_sdk(monkeypatch, handle)
    token, expiry = await github_app.get_github_app_installation_token_with_expiry(
        installation_id=3,
        repository_ids=[123],
        repositories=["ignored"],
        permissions={"workflows": "write", "contents": "write"},
    )
    assert (token, expiry) == ("token", "2099-01-01T00:00:00Z")


@pytest.mark.parametrize("status", [403, 500])
async def test_token_failure_never_returns_cached_broader_access(
    monkeypatch: pytest.MonkeyPatch, status: int
) -> None:
    def handle(request: httpx2.Request) -> httpx2.Response:
        if json.loads(request.content).get("repository_ids"):
            return httpx2.Response(status, json={"message": "unavailable"})
        return httpx2.Response(
            201, json={"token": "broad-token", "expires_at": "2099-01-01T00:00:00Z"}
        )

    mock_github_sdk(monkeypatch, handle)
    assert await github_app.get_github_app_installation_token() == "broad-token"
    assert await github_app.get_github_app_installation_token(repository_ids=[123]) is None


async def test_unknown_permissions_cannot_be_silently_dropped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handle(request: httpx2.Request) -> httpx2.Response:
        pytest.fail("Invalid permissions must not reach GitHub")

    mock_github_sdk(monkeypatch, handle)
    assert (
        await github_app.get_github_app_installation_token(permissions={"unknown": "read"}) is None
    )


async def test_langsmith_run_cannot_borrow_a_cached_installation_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expiry = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    mock_github_sdk(
        monkeypatch,
        lambda request: httpx2.Response(201, json={"token": "cached-secret", "expires_at": expiry}),
    )
    assert await github_app.get_github_app_installation_token() == "cached-secret"

    async def mint(runtime: Runtime) -> str | None:
        return await github_app.get_github_app_installation_token()

    with pytest.raises(PermissionError, match="cannot use GitHub App credentials"):
        await in_graph_context({"configurable": {"github_linked": False}}, mint)


async def test_forced_refresh_keeps_a_concurrently_minted_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pending = asyncio.Event()
    release = asyncio.Event()
    requests = 0
    expiry = (datetime.now(UTC) + timedelta(hours=1)).isoformat()

    async def handle(request: httpx2.Request) -> httpx2.Response:
        nonlocal requests
        requests += 1
        number = requests
        if number == 2:
            pending.set()
            await release.wait()
        return httpx2.Response(201, json={"token": f"token-{number}", "expires_at": expiry})

    mock_github_sdk(monkeypatch, lambda request: httpx2.Response(500))
    monkeypatch.setattr(
        sdk, "GitHub", partial(GitHub, async_transport=httpx2.MockTransport(handle))
    )
    assert await github_app.get_github_app_installation_token() == "token-1"
    refreshing = asyncio.create_task(
        github_app.get_github_app_installation_token_with_expiry(force_refresh=True)
    )
    await pending.wait()
    try:
        newer, _ = await github_app.get_github_app_installation_token_with_expiry(
            force_refresh=True
        )
    finally:
        release.set()
    refreshed, _ = await refreshing
    assert refreshed == newer == "token-3"
    assert await github_app.get_github_app_installation_token() == "token-3"
