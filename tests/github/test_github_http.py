"""Unit tests for the shared GitHub HTTP helper."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from agent.utils.github_http import (
    GITHUB_API_BASE,
    GITHUB_GRAPHQL,
    _compute_backoff,
    _is_retryable_response,
    _is_secondary_rate_limit,
    _retry_after_seconds,
    github_client,
    github_headers,
    github_request,
)


def test_github_headers_returns_standard_headers() -> None:
    headers = github_headers("mytoken")
    assert headers["Authorization"] == "Bearer mytoken"
    assert headers["Accept"] == "application/vnd.github+json"
    assert headers["X-GitHub-Api-Version"] == "2022-11-28"


def test_github_constants() -> None:
    assert GITHUB_API_BASE == "https://api.github.com"
    assert GITHUB_GRAPHQL == "https://api.github.com/graphql"


def _make_response(status_code: int, headers: dict[str, str] | None = None) -> httpx.Response:
    return httpx.Response(status_code, headers=headers or {})


class TestIsSecondaryRateLimit:
    def test_403_with_rate_limit_remaining_zero(self) -> None:
        resp = _make_response(403, {"X-RateLimit-Remaining": "0"})
        assert _is_secondary_rate_limit(resp)

    def test_403_with_secondary_rate_limit_body(self) -> None:
        resp = httpx.Response(403, text="You have exceeded a secondary rate limit")
        assert _is_secondary_rate_limit(resp)

    def test_403_with_rate_limit_body(self) -> None:
        resp = httpx.Response(403, text="API rate limit exceeded")
        assert _is_secondary_rate_limit(resp)

    def test_403_without_rate_limit_indicators(self) -> None:
        resp = httpx.Response(403, text="Forbidden")
        assert not _is_secondary_rate_limit(resp)

    def test_non_403_not_secondary_rate_limit(self) -> None:
        resp = _make_response(429, {"X-RateLimit-Remaining": "0"})
        assert not _is_secondary_rate_limit(resp)


class TestIsRetryableResponse:
    @pytest.mark.parametrize("status", [429, 503])
    def test_always_retryable_status_codes(self, status: int) -> None:
        assert _is_retryable_response(_make_response(status), "POST")
        assert _is_retryable_response(_make_response(status), "GET")

    @pytest.mark.parametrize("status", [502, 504])
    def test_idempotent_only_retryable_status_codes(self, status: int) -> None:
        assert _is_retryable_response(_make_response(status), "GET")
        assert not _is_retryable_response(_make_response(status), "POST")

    def test_secondary_rate_limit_is_retryable(self) -> None:
        resp = httpx.Response(403, text="secondary rate limit")
        assert _is_retryable_response(resp, "POST")
        assert _is_retryable_response(resp, "GET")

    def test_200_not_retryable(self) -> None:
        assert not _is_retryable_response(_make_response(200), "GET")

    def test_404_not_retryable(self) -> None:
        assert not _is_retryable_response(_make_response(404), "GET")

    def test_422_not_retryable(self) -> None:
        assert not _is_retryable_response(_make_response(422), "POST")


class TestRetryAfterSeconds:
    def test_valid_retry_after(self) -> None:
        resp = _make_response(429, {"Retry-After": "30"})
        assert _retry_after_seconds(resp) == 30.0

    def test_no_retry_after_header(self) -> None:
        resp = _make_response(429)
        assert _retry_after_seconds(resp) is None

    def test_invalid_retry_after(self) -> None:
        resp = _make_response(429, {"Retry-After": "not-a-number"})
        assert _retry_after_seconds(resp) is None


class TestComputeBackoff:
    def test_uses_retry_after_when_present(self) -> None:
        resp = _make_response(429, {"Retry-After": "15"})
        delay = _compute_backoff(resp, attempt=0)
        assert delay == 15.0

    def test_caps_retry_after_at_max(self) -> None:
        resp = _make_response(429, {"Retry-After": "120"})
        delay = _compute_backoff(resp, attempt=0)
        assert delay <= 60.0

    def test_exponential_backoff_without_response(self) -> None:
        delay = _compute_backoff(None, attempt=0)
        assert 0.75 <= delay <= 1.25

    def test_exponential_backoff_attempt_2(self) -> None:
        delay = _compute_backoff(None, attempt=2)
        base = 1.0 * (2**2)
        assert base - base * 0.25 <= delay <= base + base * 0.25

    def test_backoff_capped_at_max(self) -> None:
        delay = _compute_backoff(None, attempt=10)
        assert delay <= 60.0


@pytest.mark.asyncio
async def test_github_client_yields_client_with_token_headers() -> None:
    async with github_client(token="testtoken") as client:
        assert isinstance(client, httpx.AsyncClient)
        assert client.headers["Authorization"] == "Bearer testtoken"
        assert client.headers["Accept"] == "application/vnd.github+json"


@pytest.mark.asyncio
async def test_github_client_without_token() -> None:
    async with github_client() as client:
        assert isinstance(client, httpx.AsyncClient)
        assert "Authorization" not in client.headers


@pytest.mark.asyncio
async def test_github_request_retries_on_429() -> None:
    responses = [
        _make_response(429, {"Retry-After": "0"}),
        _make_response(200),
    ]
    client = AsyncMock()
    client.get = AsyncMock(side_effect=responses)

    with patch("agent.utils.github_http.asyncio.sleep", new_callable=AsyncMock):
        response = await github_request(client, "GET", "https://api.github.com/test")

    assert response.status_code == 200
    assert client.get.await_count == 2


@pytest.mark.asyncio
async def test_github_request_retries_on_503() -> None:
    responses = [
        _make_response(503),
        _make_response(200),
    ]
    client = AsyncMock()
    client.post = AsyncMock(side_effect=responses)

    with patch("agent.utils.github_http.asyncio.sleep", new_callable=AsyncMock):
        response = await github_request(client, "POST", "https://api.github.com/test")

    assert response.status_code == 200
    assert client.post.await_count == 2


@pytest.mark.asyncio
async def test_github_request_retries_on_secondary_rate_limit() -> None:
    responses = [
        httpx.Response(403, text="secondary rate limit", headers={}),
        _make_response(200),
    ]
    client = AsyncMock()
    client.get = AsyncMock(side_effect=responses)

    with patch("agent.utils.github_http.asyncio.sleep", new_callable=AsyncMock):
        response = await github_request(client, "GET", "https://api.github.com/test")

    assert response.status_code == 200
    assert client.get.await_count == 2


@pytest.mark.asyncio
async def test_github_request_does_not_retry_on_404() -> None:
    response_404 = _make_response(404)
    client = AsyncMock()
    client.get = AsyncMock(return_value=response_404)

    response = await github_request(client, "GET", "https://api.github.com/test")

    assert response.status_code == 404
    assert client.get.await_count == 1


@pytest.mark.asyncio
async def test_github_request_does_not_retry_on_422() -> None:
    response_422 = _make_response(422)
    client = AsyncMock()
    client.post = AsyncMock(return_value=response_422)

    response = await github_request(client, "POST", "https://api.github.com/test")

    assert response.status_code == 422
    assert client.post.await_count == 1


@pytest.mark.asyncio
async def test_github_request_gives_up_after_max_retries() -> None:
    response_429 = _make_response(429, {"Retry-After": "0"})
    client = AsyncMock()
    client.get = AsyncMock(return_value=response_429)

    with patch("agent.utils.github_http.asyncio.sleep", new_callable=AsyncMock):
        response = await github_request(client, "GET", "https://api.github.com/test", max_retries=2)

    assert response.status_code == 429
    assert client.get.await_count == 3


@pytest.mark.asyncio
async def test_github_request_retries_on_timeout() -> None:
    responses = [
        httpx.TimeoutException("timeout"),
        _make_response(200),
    ]
    client = AsyncMock()
    client.get = AsyncMock(side_effect=responses)

    with patch("agent.utils.github_http.asyncio.sleep", new_callable=AsyncMock):
        response = await github_request(client, "GET", "https://api.github.com/test")

    assert response.status_code == 200
    assert client.get.await_count == 2


@pytest.mark.asyncio
async def test_github_request_does_not_retry_transport_error_on_post() -> None:
    """Transport errors on POST must not retry — the server may have already
    processed the write, and retrying would duplicate the resource."""
    client = AsyncMock()
    client.post = AsyncMock(side_effect=httpx.TimeoutException("timeout"))

    with pytest.raises(httpx.TimeoutException):
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

    with patch("agent.utils.github_http.asyncio.sleep", new_callable=AsyncMock):
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

    with patch("agent.utils.github_http.asyncio.sleep", new_callable=AsyncMock):
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

    with patch("agent.utils.github_http.asyncio.sleep", new_callable=AsyncMock):
        response = await github_request(client, "GET", "https://api.github.com/test")

    assert response.status_code == 200
    assert client.get.await_count == 2


@pytest.mark.asyncio
async def test_github_request_raises_after_exhausting_transport_retries() -> None:
    client = AsyncMock()
    client.get = AsyncMock(side_effect=httpx.ConnectTimeout("timeout"))

    with patch("agent.utils.github_http.asyncio.sleep", new_callable=AsyncMock):
        with pytest.raises(httpx.ConnectTimeout):
            await github_request(client, "GET", "https://api.github.com/test", max_retries=1)

    assert client.get.await_count == 2


@pytest.mark.asyncio
async def test_github_request_propagates_non_retryable_http_error() -> None:
    client = AsyncMock()
    client.get = AsyncMock(side_effect=httpx.HTTPError("boom"))

    with pytest.raises(httpx.HTTPError):
        await github_request(client, "GET", "https://api.github.com/test")

    assert client.get.await_count == 1
