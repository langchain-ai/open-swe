from __future__ import annotations

from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import HTTPException

from agent.dashboard import routes


@pytest.mark.asyncio
async def test_paginate_converts_github_timeout_to_503() -> None:
    request = httpx.Request("GET", "https://api.github.com/user/installations")

    async def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("connect timed out", request=request)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(HTTPException) as exc:
            await routes._paginate(
                client,
                "https://api.github.com/user/installations",
                headers={},
                items_key="installations",
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
            await routes._paginate(
                client,
                "https://api.github.com/user/installations",
                headers={},
                items_key="installations",
            )

    assert exc.value.status_code == 502
    assert exc.value.detail == "github API error (500)"


@pytest.mark.asyncio
async def test_list_repos_propagates_repository_page_timeouts(monkeypatch) -> None:
    monkeypatch.setattr(routes, "get_valid_access_token", AsyncMock(return_value="token"))
    calls = 0

    async def fake_paginate(*args: object, **kwargs: object) -> list[dict[str, object]]:
        nonlocal calls
        calls += 1
        if calls == 1:
            return [{"id": 123, "account": {"login": "acme", "type": "Organization"}}]
        raise HTTPException(503, "github API request timed out")

    monkeypatch.setattr(routes, "_paginate", fake_paginate)

    with pytest.raises(HTTPException) as exc:
        await routes.list_repos(session={"sub": "octocat"})

    assert exc.value.status_code == 503
    assert exc.value.detail == "github API request timed out"


@pytest.mark.asyncio
async def test_list_repos_skips_inaccessible_installations(monkeypatch) -> None:
    monkeypatch.setattr(routes, "get_valid_access_token", AsyncMock(return_value="token"))
    calls = 0

    async def fake_paginate(*args: object, **kwargs: object) -> list[dict[str, object]]:
        nonlocal calls
        calls += 1
        if calls == 1:
            return [{"id": 123, "account": {"login": "acme", "type": "Organization"}}]
        raise HTTPException(403, "github API forbidden")

    monkeypatch.setattr(routes, "_paginate", fake_paginate)

    result = await routes.list_repos(session={"sub": "octocat"})

    assert result == {
        "installations": [{"id": 123, "account": "acme", "account_type": "Organization"}],
        "repositories": [],
    }
