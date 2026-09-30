import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from agent.github import app as github_app
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
    def handle(request: httpx.Request) -> httpx.Response:
        expected = (
            "/orgs/secondary%2Forg/installation"
            if kind == "org"
            else "/repos/acme/private%2Frepo/installation"
        )
        assert request.url.raw_path.decode() == expected
        assert request.headers["authorization"] == "Bearer test-app-jwt"
        return httpx.Response(200, json={"id": 3})

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
        monkeypatch, lambda request: httpx.Response(status, json={"message": "unavailable"})
    )
    assert await github_app.get_github_app_installation_id_for_org("acme") is None
    assert await github_app.get_github_app_installation_id_for_repo("acme", "api") is None


@pytest.mark.parametrize("minutes,expected_requests", [(60, 1), (2, 2)])
async def test_token_cache_respects_expiry(
    monkeypatch: pytest.MonkeyPatch, minutes: int, expected_requests: int
) -> None:
    expires_at = (datetime.now(UTC) + timedelta(minutes=minutes)).isoformat()
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            201, json={"token": f"token-{len(requests)}", "expires_at": expires_at}
        )

    mock_github_sdk(monkeypatch, handle)
    first, expiry = await github_app.get_github_app_installation_token_with_expiry()
    second, _ = await github_app.get_github_app_installation_token_with_expiry()
    assert first == "token-1"
    assert second == f"token-{expected_requests}"
    assert expiry == expires_at
    assert len(requests) == expected_requests


async def test_cache_separates_repository_installation_and_permission_scopes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expires_at = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    requests: list[tuple[str, dict[str, object]]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append((request.url.path, json.loads(request.content)))
        return httpx.Response(
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


async def test_repository_names_and_full_installation_have_distinct_scopes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scopes: list[dict[str, object]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        scopes.append(json.loads(request.content))
        return httpx.Response(
            201, json={"token": f"token-{len(scopes)}", "expires_at": "2099-01-01T00:00:00Z"}
        )

    mock_github_sdk(monkeypatch, handle)
    for names in (["a"], ["b"], ["a"], None):
        assert await github_app.get_github_app_installation_token(repositories=names)
    assert scopes == [{"repositories": ["a"]}, {"repositories": ["b"]}, {}]


async def test_token_request_preserves_repository_ids_and_permissions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/app/installations/3/access_tokens"
        assert json.loads(request.content) == {
            "repository_ids": [123],
            "permissions": {"contents": "write", "workflows": "write"},
        }
        return httpx.Response(201, json={"token": "token", "expires_at": "2099-01-01T00:00:00Z"})

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
    def handle(request: httpx.Request) -> httpx.Response:
        if json.loads(request.content).get("repository_ids"):
            return httpx.Response(status, json={"message": "unavailable"})
        return httpx.Response(
            201, json={"token": "broad-token", "expires_at": "2099-01-01T00:00:00Z"}
        )

    mock_github_sdk(monkeypatch, handle)
    assert await github_app.get_github_app_installation_token() == "broad-token"
    assert await github_app.get_github_app_installation_token(repository_ids=[123]) is None


async def test_unknown_permissions_cannot_be_silently_dropped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        pytest.fail("Invalid permissions must not reach GitHub")

    mock_github_sdk(monkeypatch, handle)
    assert (
        await github_app.get_github_app_installation_token(permissions={"unknown": "read"}) is None
    )
