from __future__ import annotations

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
