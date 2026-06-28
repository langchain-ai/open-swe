from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
import pytest

from agent.utils.github_merge import merge_pull_request


@pytest.mark.asyncio
async def test_merge_pull_request_sends_sha_and_merge_method() -> None:
    captured: dict[str, Any] = {}

    @asynccontextmanager
    async def client_factory(**kwargs: Any) -> AsyncIterator[object]:
        captured["client_kwargs"] = kwargs
        yield object()

    async def request_func(client: object, method: str, url: str, **kwargs: Any) -> httpx.Response:
        captured["client"] = client
        captured["method"] = method
        captured["url"] = url
        captured["request_kwargs"] = kwargs
        return httpx.Response(200, json={"merged": True, "sha": "merge-sha"})

    result = await merge_pull_request(
        owner="octo",
        repo="repo",
        pr_number=7,
        token="token",
        sha="head-sha",
        merge_method="squash",
        client_factory=client_factory,
        request_func=request_func,
    )

    assert result.success is True
    assert result.status == "merged"
    assert result.sha == "merge-sha"
    assert captured["client_kwargs"] == {"token": "token"}
    assert captured["method"] == "PUT"
    assert captured["url"] == "https://api.github.com/repos/octo/repo/pulls/7/merge"
    assert captured["request_kwargs"]["json"] == {
        "sha": "head-sha",
        "merge_method": "squash",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [405, 409])
async def test_merge_pull_request_merge_blocked(status_code: int) -> None:
    async def request_func(
        _client: object, _method: str, _url: str, **_kwargs: Any
    ) -> httpx.Response:
        return httpx.Response(status_code, json={"message": "Pull Request is not mergeable"})

    result = await merge_pull_request(
        owner="octo",
        repo="repo",
        pr_number=7,
        token="token",
        sha="head-sha",
        client_factory=_client_factory,
        request_func=request_func,
    )

    assert result.success is False
    assert result.status == "blocked"
    assert result.reason == "github_merge_blocked"
    assert result.http_status == status_code
    assert result.details["message"] == "Pull Request is not mergeable"


@pytest.mark.asyncio
async def test_merge_pull_request_validation_error_is_structured() -> None:
    async def request_func(
        _client: object, _method: str, _url: str, **_kwargs: Any
    ) -> httpx.Response:
        return httpx.Response(422, json={"message": "Validation Failed"})

    result = await merge_pull_request(
        owner="octo",
        repo="repo",
        pr_number=7,
        token="token",
        sha="head-sha",
        client_factory=_client_factory,
        request_func=request_func,
    )

    assert result.success is False
    assert result.status == "error"
    assert result.reason == "github_merge_rejected"
    assert result.http_status == 422
    assert result.details["message"] == "Validation Failed"


@pytest.mark.asyncio
async def test_merge_pull_request_provider_error_is_structured() -> None:
    async def request_func(
        _client: object, _method: str, _url: str, **_kwargs: Any
    ) -> httpx.Response:
        return httpx.Response(500, json={"message": "provider unavailable"})

    result = await merge_pull_request(
        owner="octo",
        repo="repo",
        pr_number=7,
        token="token",
        sha="head-sha",
        client_factory=_client_factory,
        request_func=request_func,
    )

    assert result.success is False
    assert result.status == "error"
    assert result.reason == "github_merge_failed"
    assert result.http_status == 500
    assert result.details["message"] == "provider unavailable"


@asynccontextmanager
async def _client_factory(**_: Any) -> AsyncIterator[object]:
    yield object()
