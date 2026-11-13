import asyncio
from unittest.mock import AsyncMock, MagicMock

import httpx2
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from openswe.api.github_errors import add_github_error_handlers
from openswe.dashboard import repo_access
from openswe.github import dashboard_routes, http, repo_cache, repos
from openswe.github.http import GitHubClient
from openswe.users import User
from tests.support.github_sdk import mock_github_sdk

_read_cached_repos = repo_cache.read_cached_repos
_write_cached_repos = repo_cache.write_cached_repos


@pytest.fixture(autouse=True)
def _no_repo_cache(monkeypatch) -> None:
    """Default every test to a cache miss with writes swallowed."""
    monkeypatch.setattr(dashboard_routes, "read_cached_repos", AsyncMock(return_value=None))
    monkeypatch.setattr(repo_cache, "read_cached_repos", AsyncMock(return_value=None))
    monkeypatch.setattr(repo_cache, "write_cached_repos", AsyncMock(return_value=None))


@pytest.fixture
def real_repo_cache(registry_db: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(repo_cache, "read_cached_repos", _read_cached_repos)
    monkeypatch.setattr(repo_cache, "write_cached_repos", _write_cached_repos)
    monkeypatch.setenv("ALLOWED_GITHUB_USERS", "ada,bob")
    monkeypatch.setenv("ALLOWED_GITHUB_ORGS", "")


async def test_list_repos_refreshes_expired_user_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        repos, "get_valid_access_token", AsyncMock(side_effect=["expired", "fresh"])
    )

    def handle(request: httpx2.Request) -> httpx2.Response:
        if request.headers["authorization"].split()[-1] == "expired":
            return httpx2.Response(401, json={"message": "Bad credentials"})
        assert request.headers["authorization"].split()[-1] == "fresh"
        return httpx2.Response(200, json={"installations": []})

    mock_github_sdk(monkeypatch, handle)
    assert await repos.fetch_user_installations_and_repos("octocat") == ([], [])


@pytest.mark.parametrize("operation", ["list", "access", "client"])
def test_missing_github_authorization_returns_direct_reconnect_without_clearing_session(
    monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    monkeypatch.setenv("DASHBOARD_API_BASE_URL", "https://api.example/prefix")
    monkeypatch.setattr(repos, "get_valid_access_token", AsyncMock(return_value=None))
    monkeypatch.setattr(
        "openswe.dashboard.profiles.get_valid_access_token", AsyncMock(return_value=None)
    )
    app = FastAPI()
    add_github_error_handlers(app)

    @app.get("/protected")
    async def protected() -> None:
        if operation == "list":
            await repos.fetch_user_installations_and_repos("octocat")
        elif operation == "access":
            await repo_access.require_repo_access_for_user("octocat", "acme/api")
        else:
            async with GitHubClient.as_user("octocat"):
                pytest.fail("Missing OAuth must not fall back to App credentials")

    with TestClient(app) as client:
        client.cookies.set("osw_session", "existing-session")
        response = client.get("/protected")
        assert response.status_code == 401
        detail = response.json()["detail"]
        assert "octocat" in detail
        assert "https://api.example/prefix/my-settings/connections" in detail
        assert client.cookies.get("osw_session") == "existing-session"
        assert "set-cookie" not in response.headers


@pytest.mark.parametrize(
    ("status", "message", "headers", "expected"),
    [
        (403, "API rate limit exceeded", {}, 429),
        (403, "secondary rate limit", {}, 429),
        (403, "Forbidden", {"X-RateLimit-Remaining": "0"}, 429),
        (403, "Forbidden", {"Retry-After": "60"}, 429),
        (429, "Too many requests", {"Retry-After": "30"}, 429),
        (429, "Too many requests", {"Retry-After": "invalid"}, 429),
        (403, "Resource not accessible", {}, 403),
        (401, "Bad credentials", {}, 401),
        (404, "Not found", {}, 404),
        (200, "", {}, 200),
    ],
)
def test_repo_access_distinguishes_rate_limits_from_permission_denials(
    status: int, message: str, headers: dict[str, str], expected: int
) -> None:
    app = FastAPI()

    @app.get("/protected")
    async def protected() -> None:
        repo_access.RepoAccessResponse.require(
            httpx2.Response(
                status,
                json={"message": message},
                headers=headers,
                request=httpx2.Request("GET", "https://api.github.com/repos/acme/api"),
            )
        )

    with TestClient(app) as client:
        response = client.get("/protected")
    assert response.status_code == expected
    if expected == 429:
        assert "rate limit" in response.json()["detail"]
        retry_after = headers.get("Retry-After", "")
        assert response.headers.get("Retry-After") == (
            retry_after if retry_after.isascii() and retry_after.isdecimal() else None
        )
    elif expected == 403:
        assert response.json()["detail"] == "no access to this private repository"


@pytest.mark.parametrize("recovers", [True, False])
@pytest.mark.parametrize(
    "error_type", [httpx2.RemoteProtocolError, httpx2.ReadTimeout, 502, 503, 504]
)
async def test_repo_access_retries_transport_failures_and_fails_closed(
    monkeypatch: pytest.MonkeyPatch, recovers: bool, error_type: type[httpx2.TransportError] | int
) -> None:
    attempts = 0

    def handle(request: httpx2.Request) -> httpx2.Response:
        nonlocal attempts
        attempts += 1
        assert request.method == "GET"
        assert request.url.path == "/repos/acme/api"
        assert request.headers["authorization"] == "Bearer token"
        if recovers and attempts > 1:
            return httpx2.Response(200, json={"full_name": "acme/api"})
        if isinstance(error_type, int):
            return httpx2.Response(error_type, json={"message": "Temporary server failure"})
        raise error_type("Server disconnected without sending a response", request=request)

    monkeypatch.setattr(http, "GITHUB_TRANSPORT", httpx2.MockTransport(handle))
    monkeypatch.setattr(http.asyncio, "sleep", AsyncMock())
    if recovers:
        assert await repo_access.assert_repo_access("acme/api", "token") == "acme/api"
        assert attempts == 2
    else:
        with pytest.raises(HTTPException) as excinfo:
            await repo_access.assert_repo_access("acme/api", "token")
        assert excinfo.value.status_code == (502 if isinstance(error_type, int) else 503)
        assert attempts == http.DEFAULT_MAX_RETRIES + 1


@pytest.mark.parametrize(
    "status,message,headers",
    [
        (401, "Refused", {}),
        (403, "Refused", {}),
        (404, "Refused", {}),
        (429, "Refused", {"Retry-After": "60"}),
        (403, "Secondary rate limit", {"Retry-After": "60"}),
        (403, "Refused", {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "60"}),
    ],
)
async def test_repo_access_preserves_github_refusals(
    monkeypatch: pytest.MonkeyPatch, status: int, message: str, headers: dict[str, str]
) -> None:
    attempts = 0

    def handle(request: httpx2.Request) -> httpx2.Response:
        nonlocal attempts
        attempts += 1
        return httpx2.Response(
            status,
            json={"message": message},
            headers=headers,
        )

    monkeypatch.setattr(http, "GITHUB_TRANSPORT", httpx2.MockTransport(handle))
    sleep = AsyncMock()
    monkeypatch.setattr(http.asyncio, "sleep", sleep)
    monkeypatch.setattr(http.time, "time", lambda: 0)
    with pytest.raises(HTTPException) as excinfo:
        await repo_access.assert_repo_access("acme/api", "token")
    rate_limited = status == 429 or "rate limit" in message.lower() or bool(headers)
    assert excinfo.value.status_code == (429 if rate_limited else status)
    assert attempts == 1
    sleep.assert_not_awaited()
    if "Retry-After" in headers:
        assert excinfo.value.headers == {"Retry-After": "60"}


async def test_parallel_access_checks_share_one_refresh_and_never_outlive_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stored = {"token": "expired"}
    checks: list[str] = []

    async def token(login: str, *, force_refresh: bool = False) -> str:
        if force_refresh:
            stored["token"] = "fresh"
        return stored["token"]

    def handle(request: httpx2.Request) -> httpx2.Response:
        current = request.headers["authorization"].removeprefix("Bearer ")
        checks.append(current)
        if current == "revoked":
            return httpx2.Response(404, json={"message": "Not found"})
        if current == "expired":
            return httpx2.Response(401, json={"message": "Bad credentials"})
        return httpx2.Response(200, json={"full_name": "acme/api"})

    refresh = AsyncMock(side_effect=token)
    monkeypatch.setattr("openswe.dashboard.profiles.get_valid_access_token", refresh)
    monkeypatch.setattr(http, "GITHUB_TRANSPORT", httpx2.MockTransport(handle))

    tokens = await asyncio.gather(
        *(repo_access.require_repo_access_for_user("octocat", "acme/api") for _ in range(3))
    )

    assert tokens == ["fresh"] * 3
    assert checks == ["expired", "fresh"]
    assert refresh.await_count == 2
    refresh.assert_awaited_with("octocat", force_refresh=True)

    stored["token"] = "revoked"
    with pytest.raises(HTTPException) as excinfo:
        await repo_access.require_repo_access_for_user("octocat", "acme/api")
    assert excinfo.value.status_code == 404


@pytest.mark.parametrize(
    ("github_status", "expected", "detail"),
    [
        (None, 503, "workspace GitHub App token unavailable"),
        (200, None, None),
        (401, 502, "workspace GitHub App token rejected"),
        (403, 403, "repository unavailable to the workspace GitHub App"),
        (404, 404, "repository unavailable to the workspace GitHub App"),
        (429, 429, "GitHub API rate limit reached"),
    ],
)
async def test_workspace_access_uses_default_app_identity(
    monkeypatch: pytest.MonkeyPatch,
    github_status: int | None,
    expected: int | None,
    detail: str | None,
) -> None:
    app_token = AsyncMock(return_value="workspace-token" if github_status else None)
    monkeypatch.setattr("openswe.github.app.get_github_app_installation_token", app_token)
    monkeypatch.setattr(
        "openswe.dashboard.profiles.get_valid_access_token",
        AsyncMock(side_effect=AssertionError("Workspace checks must not use OAuth")),
    )

    def handle(request: httpx2.Request) -> httpx2.Response:
        assert request.headers["authorization"] == "Bearer workspace-token"
        assert request.url.path == "/repos/acme/api"
        assert github_status is not None
        return httpx2.Response(github_status, json={})

    monkeypatch.setattr(http, "GITHUB_TRANSPORT", httpx2.MockTransport(handle))
    if expected is None:
        assert await repo_access.require_repo_access_for_workspace("acme/api") == "workspace-token"
    else:
        with pytest.raises(HTTPException) as excinfo:
            await repo_access.require_repo_access_for_workspace("acme/api")
        assert excinfo.value.status_code == expected
        assert detail is not None
        assert detail in excinfo.value.detail
    app_token.assert_awaited_once_with(
        installation_id=None, repositories=None, permissions=None, raise_errors=True
    )


async def test_list_repos_follows_installation_and_repository_next_links(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(repos, "get_valid_access_token", AsyncMock(return_value="token"))

    def handle(request: httpx2.Request) -> httpx2.Response:
        assert request.headers["x-github-api-version"] == "2022-11-28"
        if request.url.path == "/user/installations":
            if request.url.params.get("page") == "2":
                return httpx2.Response(200, json={"installations": [{"id": 456, "account": None}]})
            return httpx2.Response(
                200,
                json={"installations": [{"id": 123, "account": None}]},
                headers={"Link": '<https://api.github.com/user/installations?page=2>; rel="next"'},
            )
        if request.url.path == "/user/installations/123/repositories":
            if request.url.params.get("page") == "2":
                return httpx2.Response(
                    200,
                    json={
                        "repositories": [
                            {"full_name": "acme/later", "private": True, "archived": True}
                        ]
                    },
                )
            return httpx2.Response(
                200,
                json={"repositories": [{"full_name": "acme/first", "private": False}]},
                headers={
                    "Link": '<https://api.github.com/user/installations/123/repositories?page=2>; rel="next"'
                },
            )
        return httpx2.Response(
            200, json={"repositories": [{"full_name": "other/api", "private": True}]}
        )

    mock_github_sdk(monkeypatch, handle)
    payload = await dashboard_routes.list_repos(session={"sub": "octocat"})
    assert payload["repositories"] == [
        {"full_name": "acme/first", "private": False, "archived": False},
        {"full_name": "acme/later", "private": True, "archived": True},
        {"full_name": "other/api", "private": True, "archived": False},
    ]
    assert await repos.accessible_repo_full_names("octocat") == {
        "acme/first",
        "acme/later",
        "other/api",
    }


async def test_access_checks_observe_removed_repositories_without_http_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(repos, "get_valid_access_token", AsyncMock(return_value="token"))
    allowed = True

    def handle(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/user/installations":
            return httpx2.Response(200, json={"installations": [{"id": 123, "account": None}]})
        return httpx2.Response(
            200,
            json={"repositories": [{"full_name": "Acme/API", "private": True}] if allowed else []},
            headers={"Cache-Control": "public, max-age=3600"},
        )

    mock_github_sdk(monkeypatch, handle)
    assert await repos.accessible_repo_full_names("octocat") == {"acme/api"}
    allowed = False
    assert await repos.accessible_repo_full_names("octocat") == set()


@pytest.mark.asyncio
async def test_list_repos_serves_stale_cache_and_schedules_refresh(monkeypatch) -> None:
    cached = {"installations": [], "repositories": [{"full_name": "acme/api", "private": True}]}
    monkeypatch.setattr(
        dashboard_routes,
        "read_cached_repos",
        AsyncMock(return_value=(cached, dashboard_routes.REPO_LIST_FRESH_MS + 1)),
    )
    fetch = AsyncMock(return_value=([], []))
    monkeypatch.setattr(repos, "fetch_user_installations_and_repos", fetch)
    schedule = MagicMock()
    monkeypatch.setattr(dashboard_routes, "schedule_repo_cache_refresh", schedule)

    result = await dashboard_routes.list_repos(session={"sub": "octocat"})

    assert result == cached
    fetch.assert_not_awaited()
    assert schedule.call_args.args[0] == "octocat"


@pytest.mark.asyncio
async def test_schedule_repo_cache_refresh_runs_once_per_login() -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def refresh() -> None:
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()

    repo_cache.schedule_repo_cache_refresh("Octocat", refresh)
    await started.wait()
    repo_cache.schedule_repo_cache_refresh("octocat", refresh)
    release.set()
    await asyncio.sleep(0)
    await asyncio.gather(*list(repo_cache._refresh_tasks))

    assert calls == 1
    assert "octocat" not in repo_cache._refreshing


@pytest.mark.usefixtures("real_repo_cache")
async def test_repo_cache_round_trips_per_person() -> None:
    await User.sign_in("github", "1", login="ada")
    await User.sign_in("github", "2", login="bob")
    payload = {"installations": [], "repositories": [{"full_name": "acme/api"}]}

    await repo_cache.write_cached_repos("Ada", payload)

    cached = await repo_cache.read_cached_repos("ada")
    assert cached is not None and cached[0] == payload
    assert await repo_cache.read_cached_repos("bob") is None


@pytest.mark.usefixtures("real_repo_cache")
async def test_access_trusts_a_cached_listing_briefly_and_drops_it_on_installation_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await User.sign_in("github", "1", login="ada")
    fetch = AsyncMock(side_effect=[([], [{"full_name": "Acme/API"}]), ([], []), ([], [])])
    monkeypatch.setattr(repos, "fetch_user_installations_and_repos", fetch)

    assert await repos.accessible_repo_full_names("ada") == {"acme/api"}
    assert await repos.accessible_repo_full_names("ada") == {"acme/api"}
    assert fetch.await_count == 1

    await repo_cache.forget_cached_repos()
    assert await repos.accessible_repo_full_names("ada") == set()

    monkeypatch.setattr(repos, "ACCESS_FRESH_MS", -1)
    await repos.accessible_repo_full_names("ada")
    assert fetch.await_count == 3
