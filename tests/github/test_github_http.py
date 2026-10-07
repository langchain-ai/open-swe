"""Unit tests for the shared GitHub HTTP helper."""

from unittest.mock import AsyncMock, patch

import httpx2
import pytest

from openswe.github.http import (
    _compute_backoff,
    github_request,
)


def _make_response(status_code: int, headers: dict[str, str] | None = None) -> httpx2.Response:
    return httpx2.Response(status_code, headers=headers or {})


class TestComputeBackoff:
    def test_caps_retry_after_at_max(self) -> None:
        resp = _make_response(429, {"Retry-After": "120"})
        delay = _compute_backoff(resp, attempt=0)
        assert delay <= 60.0


@pytest.mark.asyncio
async def test_github_request_retries_on_secondary_rate_limit() -> None:
    responses = [
        httpx2.Response(403, text="secondary rate limit", headers={}),
        _make_response(200),
    ]
    client = AsyncMock()
    client.get = AsyncMock(side_effect=responses)

    with patch("openswe.github.http.asyncio.sleep", new_callable=AsyncMock):
        response = await github_request(client, "GET", "https://api.github.com/test")

    assert response.status_code == 200
    assert client.get.await_count == 2


@pytest.mark.asyncio
async def test_github_request_does_not_retry_transport_error_on_post() -> None:
    """Transport errors on POST must not retry — the server may have already
    processed the write, and retrying would duplicate the resource."""
    client = AsyncMock()
    client.post = AsyncMock(side_effect=httpx2.TimeoutException("timeout"))

    with pytest.raises(httpx2.TimeoutException):
        await github_request(client, "POST", "https://api.github.com/test")

    assert client.post.await_count == 1


@pytest.mark.asyncio
async def test_github_request_retries_on_429_even_for_post() -> None:
    """429 is safe to retry for any method — the server explicitly did not
    process the request."""
    responses = [
        _make_response(429, {"Retry-After": "0"}),
        _make_response(201),
    ]
    client = AsyncMock()
    client.post = AsyncMock(side_effect=responses)

    with patch("openswe.github.http.asyncio.sleep", new_callable=AsyncMock):
        response = await github_request(client, "POST", "https://api.github.com/test")

    assert response.status_code == 201
    assert client.post.await_count == 2


@pytest.mark.asyncio
async def test_github_request_retries_on_503_even_for_post() -> None:
    responses = [
        _make_response(503),
        _make_response(201),
    ]
    client = AsyncMock()
    client.post = AsyncMock(side_effect=responses)

    with patch("openswe.github.http.asyncio.sleep", new_callable=AsyncMock):
        response = await github_request(client, "POST", "https://api.github.com/test")

    assert response.status_code == 201
    assert client.post.await_count == 2


@pytest.mark.asyncio
async def test_github_request_does_not_retry_502_on_post() -> None:
    """502 is ambiguous — the upstream may have processed the write before the
    gateway returned an error.  Must not retry for non-idempotent methods."""
    response_502 = _make_response(502)
    client = AsyncMock()
    client.post = AsyncMock(return_value=response_502)

    response = await github_request(client, "POST", "https://api.github.com/test")

    assert response.status_code == 502
    assert client.post.await_count == 1


@pytest.mark.asyncio
async def test_github_request_does_not_retry_504_on_post() -> None:
    """504 is ambiguous — the upstream may have processed the write before the
    gateway timed out.  Must not retry for non-idempotent methods."""
    response_504 = _make_response(504)
    client = AsyncMock()
    client.post = AsyncMock(return_value=response_504)

    response = await github_request(client, "POST", "https://api.github.com/test")

    assert response.status_code == 504
    assert client.post.await_count == 1


@pytest.mark.asyncio
async def test_github_request_retries_502_on_get() -> None:
    """502 is safe to retry for idempotent methods."""
    responses = [
        _make_response(502),
        _make_response(200),
    ]
    client = AsyncMock()
    client.get = AsyncMock(side_effect=responses)

    with patch("openswe.github.http.asyncio.sleep", new_callable=AsyncMock):
        response = await github_request(client, "GET", "https://api.github.com/test")

    assert response.status_code == 200
    assert client.get.await_count == 2


@pytest.mark.asyncio
async def test_github_request_raises_after_exhausting_transport_retries() -> None:
    client = AsyncMock()
    client.get = AsyncMock(side_effect=httpx2.ConnectTimeout("timeout"))

    with patch("openswe.github.http.asyncio.sleep", new_callable=AsyncMock):
        with pytest.raises(httpx2.ConnectTimeout):
            await github_request(client, "GET", "https://api.github.com/test", max_retries=1)

    assert client.get.await_count == 2


@pytest.mark.asyncio
async def test_github_request_propagates_non_retryable_http_error() -> None:
    client = AsyncMock()
    client.get = AsyncMock(side_effect=httpx2.HTTPError("boom"))

    with pytest.raises(httpx2.HTTPError):
        await github_request(client, "GET", "https://api.github.com/test")

    assert client.get.await_count == 1
