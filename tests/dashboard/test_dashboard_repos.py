import asyncio
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi import HTTPException

from agent import store
from agent.github import dashboard_routes, repo_cache, repos
from tests.support.github_sdk import mock_github_sdk


@pytest.fixture(autouse=True)
def _no_repo_cache(monkeypatch) -> None:
    """Default every test to a cache miss with writes swallowed."""
    monkeypatch.setattr(dashboard_routes, "read_cached_repos", AsyncMock(return_value=None))
    monkeypatch.setattr(dashboard_routes, "write_cached_repos", AsyncMock(return_value=None))


@pytest.mark.parametrize("page", ["installations", "repositories"])
async def test_list_repos_converts_github_timeout_to_503(
    monkeypatch: pytest.MonkeyPatch, page: str
) -> None:
    monkeypatch.setattr(repos, "get_valid_access_token", AsyncMock(return_value="token"))

    def handle(request: httpx.Request) -> httpx.Response:
        if page == "installations" or request.url.path.endswith("/repositories"):
            raise httpx.ConnectTimeout("connect timed out", request=request)
        return httpx.Response(200, json={"installations": [{"id": 123, "account": None}]})

    mock_github_sdk(monkeypatch, handle)
    with pytest.raises(HTTPException) as exc:
        await dashboard_routes.list_repos(session={"sub": "octocat"})
    assert exc.value.status_code == 503
    assert exc.value.detail == "github API request timed out"


async def test_list_repos_converts_github_status_error_to_502(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(repos, "get_valid_access_token", AsyncMock(return_value="token"))
    mock_github_sdk(
        monkeypatch, lambda request: httpx.Response(500, json={"message": "server error"})
    )
    with pytest.raises(HTTPException) as exc:
        await dashboard_routes.list_repos(session={"sub": "octocat"})
    assert exc.value.status_code == 502
    assert exc.value.detail == "github API error (500)"


@pytest.mark.parametrize("status", [403, 404])
async def test_list_repos_skips_inaccessible_installations(
    monkeypatch: pytest.MonkeyPatch, status: int
) -> None:
    monkeypatch.setattr(repos, "get_valid_access_token", AsyncMock(return_value="token"))

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/user/installations":
            return httpx.Response(
                200,
                json={
                    "installations": [
                        {"id": 123, "account": {"login": "acme", "type": "Organization"}}
                    ]
                },
            )
        return httpx.Response(status, json={"message": "unavailable"})

    mock_github_sdk(monkeypatch, handle)
    result = await dashboard_routes.list_repos(session={"sub": "octocat"})
    assert result == {
        "installations": [{"id": 123, "account": "acme", "account_type": "Organization"}],
        "repositories": [],
    }


async def test_list_repos_refreshes_expired_user_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        repos, "get_valid_access_token", AsyncMock(side_effect=["expired", "fresh"])
    )

    def handle(request: httpx.Request) -> httpx.Response:
        if request.headers["authorization"].split()[-1] == "expired":
            return httpx.Response(401, json={"message": "Bad credentials"})
        assert request.headers["authorization"].split()[-1] == "fresh"
        return httpx.Response(200, json={"installations": []})

    mock_github_sdk(monkeypatch, handle)
    assert await repos.fetch_user_installations_and_repos("octocat") == ([], [])


async def test_list_repos_follows_installation_and_repository_next_links(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(repos, "get_valid_access_token", AsyncMock(return_value="token"))

    def handle(request: httpx.Request) -> httpx.Response:
        assert request.headers["x-github-api-version"] == "2022-11-28"
        if request.url.path == "/user/installations":
            if request.url.params.get("page") == "2":
                return httpx.Response(200, json={"installations": [{"id": 456, "account": None}]})
            return httpx.Response(
                200,
                json={"installations": [{"id": 123, "account": None}]},
                headers={"Link": '<https://api.github.com/user/installations?page=2>; rel="next"'},
            )
        if request.url.path == "/user/installations/123/repositories":
            if request.url.params.get("page") == "2":
                return httpx.Response(
                    200, json={"repositories": [{"full_name": "acme/later", "private": True}]}
                )
            return httpx.Response(
                200,
                json={"repositories": [{"full_name": "acme/first", "private": False}]},
                headers={
                    "Link": '<https://api.github.com/user/installations/123/repositories?page=2>; rel="next"'
                },
            )
        return httpx.Response(
            200, json={"repositories": [{"full_name": "other/api", "private": True}]}
        )

    mock_github_sdk(monkeypatch, handle)
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

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/user/installations":
            return httpx.Response(200, json={"installations": [{"id": 123, "account": None}]})
        return httpx.Response(
            200,
            json={"repositories": [{"full_name": "Acme/API", "private": True}] if allowed else []},
            headers={"Cache-Control": "public, max-age=3600"},
        )

    mock_github_sdk(monkeypatch, handle)
    assert await repos.accessible_repo_full_names("octocat") == {"acme/api"}
    allowed = False
    assert await repos.accessible_repo_full_names("octocat") == set()


@pytest.mark.asyncio
async def test_list_repos_serves_fresh_cache_without_calling_github(monkeypatch) -> None:
    cached = {"installations": [], "repositories": [{"full_name": "acme/api", "private": True}]}
    monkeypatch.setattr(
        dashboard_routes, "read_cached_repos", AsyncMock(return_value=(cached, 1_000))
    )
    fetch = AsyncMock(return_value=([], []))
    monkeypatch.setattr(repos, "fetch_user_installations_and_repos", fetch)
    schedule = MagicMock()
    monkeypatch.setattr(dashboard_routes, "schedule_repo_cache_refresh", schedule)

    result = await dashboard_routes.list_repos(session={"sub": "octocat"})

    assert result == cached
    fetch.assert_not_awaited()
    schedule.assert_not_called()


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
async def test_list_repos_refresh_bypasses_cache_and_writes_it(monkeypatch) -> None:
    read = AsyncMock(return_value=({"installations": [], "repositories": []}, 0))
    monkeypatch.setattr(dashboard_routes, "read_cached_repos", read)
    write = AsyncMock(return_value=None)
    monkeypatch.setattr(dashboard_routes, "write_cached_repos", write)
    monkeypatch.setattr(
        repos,
        "fetch_user_installations_and_repos",
        AsyncMock(
            return_value=(
                [{"id": 123, "account": {"login": "acme", "type": "Organization"}}],
                [{"full_name": "acme/api", "private": True}],
            )
        ),
    )

    result = await dashboard_routes.list_repos(refresh=True, session={"sub": "octocat"})

    read.assert_not_awaited()
    assert result == {
        "installations": [{"id": 123, "account": "acme", "account_type": "Organization"}],
        "repositories": [{"full_name": "acme/api", "private": True}],
    }
    write.assert_awaited_once_with("octocat", result)


@pytest.mark.asyncio
async def test_read_cached_repos_rejects_expired_and_malformed_entries(monkeypatch) -> None:
    now_ms = store.now_ms()

    async def fake_get_item(namespace: list[str], key: str) -> dict[str, object]:
        assert namespace == repo_cache.REPO_LIST_CACHE_NAMESPACE
        return {"value": values[key]}

    values: dict[str, object] = {
        "fresh": {"payload": {"repositories": []}, "cached_at_ms": now_ms - 500},
        "expired": {
            "payload": {"repositories": []},
            "cached_at_ms": now_ms - repo_cache.REPO_LIST_MAX_AGE_MS - 1,
        },
        "malformed": {"payload": "nope", "cached_at_ms": now_ms},
    }
    fake_store = MagicMock()
    fake_store.get_item = fake_get_item
    monkeypatch.setattr(store, "store_client", lambda: MagicMock(store=fake_store))

    fresh = await repo_cache.read_cached_repos("fresh")
    assert fresh is not None and fresh[0] == {"repositories": []}
    assert await repo_cache.read_cached_repos("expired") is None
    assert await repo_cache.read_cached_repos("malformed") is None


@pytest.mark.asyncio
async def test_read_cached_repos_swallows_store_failures(monkeypatch) -> None:
    fake_store = MagicMock()
    fake_store.get_item = AsyncMock(side_effect=RuntimeError("store down"))
    monkeypatch.setattr(store, "store_client", lambda: MagicMock(store=fake_store))

    assert await repo_cache.read_cached_repos("octocat") is None


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
