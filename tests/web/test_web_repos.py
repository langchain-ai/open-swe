import asyncio
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from openswe.github import repo_cache, repos, web_routes
from tests.support.github_sdk import mock_github_sdk


@pytest.fixture(autouse=True)
def _no_repo_cache(monkeypatch) -> None:
    """Default every test to a cache miss with writes swallowed."""
    monkeypatch.setattr(web_routes, "read_cached_repos", AsyncMock(return_value=None))
    monkeypatch.setattr(web_routes, "write_cached_repos", AsyncMock(return_value=None))


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
                    200,
                    json={
                        "repositories": [
                            {"full_name": "acme/later", "private": True, "archived": True}
                        ]
                    },
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
    payload = await web_routes.list_repos(session={"sub": "octocat"})
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
async def test_list_repos_serves_stale_cache_and_schedules_refresh(monkeypatch) -> None:
    cached = {"installations": [], "repositories": [{"full_name": "acme/api", "private": True}]}
    monkeypatch.setattr(
        web_routes,
        "read_cached_repos",
        AsyncMock(return_value=(cached, web_routes.REPO_LIST_FRESH_MS + 1)),
    )
    fetch = AsyncMock(return_value=([], []))
    monkeypatch.setattr(repos, "fetch_user_installations_and_repos", fetch)
    schedule = MagicMock()
    monkeypatch.setattr(web_routes, "schedule_repo_cache_refresh", schedule)

    result = await web_routes.list_repos(session={"sub": "octocat"})

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
