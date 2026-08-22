import asyncio
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi import HTTPException

from agent import store
from agent.dashboard import repo_access
from agent.dashboard.routes import repos as repos_routes
from agent.settings import repo_cache


@pytest.fixture(autouse=True)
def _no_repo_cache(monkeypatch) -> None:
    """Default every test to a cache miss with writes swallowed."""
    monkeypatch.setattr(repos_routes, "read_cached_repos", AsyncMock(return_value=None))
    monkeypatch.setattr(repos_routes, "write_cached_repos", AsyncMock(return_value=None))


@pytest.mark.asyncio
async def test_paginate_converts_github_timeout_to_503() -> None:
    request = httpx.Request("GET", "https://api.github.com/user/installations")

    async def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("connect timed out", request=request)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(HTTPException) as exc:
            await repo_access.paginate_github(
                client,
                "https://api.github.com/user/installations",
                items_key="installations",
                max_retries=0,
            )

    assert exc.value.status_code == 503
    assert exc.value.detail == "github API request timed out"


@pytest.mark.asyncio
async def test_paginate_converts_github_status_error_to_502() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, request=request, json={"message": "server error"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(HTTPException) as exc:
            await repo_access.paginate_github(
                client,
                "https://api.github.com/user/installations",
                items_key="installations",
                max_retries=0,
            )

    assert exc.value.status_code == 502
    assert exc.value.detail == "github API error (500)"


@pytest.mark.asyncio
async def test_list_repos_propagates_repository_page_timeouts(monkeypatch) -> None:
    monkeypatch.setattr(repo_access, "get_valid_access_token", AsyncMock(return_value="token"))
    calls = 0

    async def fake_paginate(*args: object, **kwargs: object) -> list[dict[str, object]]:
        nonlocal calls
        calls += 1
        if calls == 1:
            return [{"id": 123, "account": {"login": "acme", "type": "Organization"}}]
        raise HTTPException(503, "github API request timed out")

    monkeypatch.setattr(repo_access, "paginate_github", fake_paginate)

    with pytest.raises(HTTPException) as exc:
        await repos_routes.list_repos(session={"sub": "octocat"})

    assert exc.value.status_code == 503
    assert exc.value.detail == "github API request timed out"


@pytest.mark.asyncio
async def test_list_repos_skips_inaccessible_installations(monkeypatch) -> None:
    monkeypatch.setattr(repo_access, "get_valid_access_token", AsyncMock(return_value="token"))
    calls = 0

    async def fake_paginate(*args: object, **kwargs: object) -> list[dict[str, object]]:
        nonlocal calls
        calls += 1
        if calls == 1:
            return [{"id": 123, "account": {"login": "acme", "type": "Organization"}}]
        raise HTTPException(403, "github API forbidden")

    monkeypatch.setattr(repo_access, "paginate_github", fake_paginate)

    result = await repos_routes.list_repos(session={"sub": "octocat"})

    assert result == {
        "installations": [{"id": 123, "account": "acme", "account_type": "Organization"}],
        "repositories": [],
    }


@pytest.mark.asyncio
async def test_list_repos_serves_fresh_cache_without_calling_github(monkeypatch) -> None:
    cached = {"installations": [], "repositories": [{"full_name": "acme/api", "private": True}]}
    monkeypatch.setattr(repos_routes, "read_cached_repos", AsyncMock(return_value=(cached, 1_000)))
    fetch = AsyncMock(return_value=([], []))
    monkeypatch.setattr(repos_routes, "fetch_user_installations_and_repos", fetch)
    schedule = MagicMock()
    monkeypatch.setattr(repos_routes, "schedule_repo_cache_refresh", schedule)

    result = await repos_routes.list_repos(session={"sub": "octocat"})

    assert result == cached
    fetch.assert_not_awaited()
    schedule.assert_not_called()


@pytest.mark.asyncio
async def test_list_repos_serves_stale_cache_and_schedules_refresh(monkeypatch) -> None:
    cached = {"installations": [], "repositories": [{"full_name": "acme/api", "private": True}]}
    monkeypatch.setattr(
        repos_routes,
        "read_cached_repos",
        AsyncMock(return_value=(cached, repos_routes.REPO_LIST_FRESH_MS + 1)),
    )
    fetch = AsyncMock(return_value=([], []))
    monkeypatch.setattr(repos_routes, "fetch_user_installations_and_repos", fetch)
    schedule = MagicMock()
    monkeypatch.setattr(repos_routes, "schedule_repo_cache_refresh", schedule)

    result = await repos_routes.list_repos(session={"sub": "octocat"})

    assert result == cached
    fetch.assert_not_awaited()
    assert schedule.call_args.args[0] == "octocat"


@pytest.mark.asyncio
async def test_list_repos_refresh_bypasses_cache_and_writes_it(monkeypatch) -> None:
    read = AsyncMock(return_value=({"installations": [], "repositories": []}, 0))
    monkeypatch.setattr(repos_routes, "read_cached_repos", read)
    write = AsyncMock(return_value=None)
    monkeypatch.setattr(repos_routes, "write_cached_repos", write)
    monkeypatch.setattr(
        repos_routes,
        "fetch_user_installations_and_repos",
        AsyncMock(
            return_value=(
                [{"id": 123, "account": {"login": "acme", "type": "Organization"}}],
                [{"full_name": "acme/api", "private": True}],
            )
        ),
    )

    result = await repos_routes.list_repos(refresh=True, session={"sub": "octocat"})

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
