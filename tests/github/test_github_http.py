"""Unit tests for the shared GitHub HTTP helper."""

import asyncio
from unittest.mock import AsyncMock, patch

import httpx2
import pytest

from openswe.dashboard import profiles
from openswe.github import http as github_http
from openswe.github.http import (
    GitHubClient,
    GitHubSignInRequired,
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
    client.request = AsyncMock(side_effect=responses)

    with patch("openswe.github.http.asyncio.sleep", new_callable=AsyncMock):
        response = await github_request(client, "GET", "https://api.github.com/test")

    assert response.status_code == 200
    assert client.request.await_count == 2


@pytest.mark.asyncio
async def test_github_request_does_not_retry_transport_error_on_post() -> None:
    """Transport errors on POST must not retry — the server may have already
    processed the write, and retrying would duplicate the resource."""
    client = AsyncMock()
    client.request = AsyncMock(side_effect=httpx2.TimeoutException("timeout"))

    with pytest.raises(httpx2.TimeoutException):
        await github_request(client, "POST", "https://api.github.com/test")

    assert client.request.await_count == 1


@pytest.mark.asyncio
async def test_github_request_retries_on_429_even_for_post() -> None:
    """429 is safe to retry for any method — the server explicitly did not
    process the request."""
    responses = [
        _make_response(429, {"Retry-After": "0"}),
        _make_response(201),
    ]
    client = AsyncMock()
    client.request = AsyncMock(side_effect=responses)

    with patch("openswe.github.http.asyncio.sleep", new_callable=AsyncMock):
        response = await github_request(client, "POST", "https://api.github.com/test")

    assert response.status_code == 201
    assert client.request.await_count == 2


@pytest.mark.asyncio
async def test_github_request_retries_on_503_even_for_post() -> None:
    responses = [
        _make_response(503),
        _make_response(201),
    ]
    client = AsyncMock()
    client.request = AsyncMock(side_effect=responses)

    with patch("openswe.github.http.asyncio.sleep", new_callable=AsyncMock):
        response = await github_request(client, "POST", "https://api.github.com/test")

    assert response.status_code == 201
    assert client.request.await_count == 2


@pytest.mark.asyncio
async def test_github_request_does_not_retry_502_on_post() -> None:
    """502 is ambiguous — the upstream may have processed the write before the
    gateway returned an error.  Must not retry for non-idempotent methods."""
    response_502 = _make_response(502)
    client = AsyncMock()
    client.request = AsyncMock(return_value=response_502)

    response = await github_request(client, "POST", "https://api.github.com/test")

    assert response.status_code == 502
    assert client.request.await_count == 1


@pytest.mark.asyncio
async def test_github_request_does_not_retry_504_on_post() -> None:
    """504 is ambiguous — the upstream may have processed the write before the
    gateway timed out.  Must not retry for non-idempotent methods."""
    response_504 = _make_response(504)
    client = AsyncMock()
    client.request = AsyncMock(return_value=response_504)

    response = await github_request(client, "POST", "https://api.github.com/test")

    assert response.status_code == 504
    assert client.request.await_count == 1


@pytest.mark.asyncio
async def test_github_request_retries_502_on_get() -> None:
    """502 is safe to retry for idempotent methods."""
    responses = [
        _make_response(502),
        _make_response(200),
    ]
    client = AsyncMock()
    client.request = AsyncMock(side_effect=responses)

    with patch("openswe.github.http.asyncio.sleep", new_callable=AsyncMock):
        response = await github_request(client, "GET", "https://api.github.com/test")

    assert response.status_code == 200
    assert client.request.await_count == 2


@pytest.mark.asyncio
async def test_github_request_raises_after_exhausting_transport_retries() -> None:
    client = AsyncMock()
    client.request = AsyncMock(side_effect=httpx2.ConnectTimeout("timeout"))

    with patch("openswe.github.http.asyncio.sleep", new_callable=AsyncMock):
        with pytest.raises(httpx2.ConnectTimeout):
            await github_request(client, "GET", "https://api.github.com/test", max_retries=1)

    assert client.request.await_count == 2


@pytest.mark.asyncio
async def test_github_request_propagates_non_retryable_http_error() -> None:
    client = AsyncMock()
    client.request = AsyncMock(side_effect=httpx2.HTTPError("boom"))

    with pytest.raises(httpx2.HTTPError):
        await github_request(client, "GET", "https://api.github.com/test")

    assert client.request.await_count == 1


async def test_as_user_refreshes_a_rejected_token_once(monkeypatch: pytest.MonkeyPatch) -> None:
    tokens = AsyncMock(side_effect=["stale", "fresh"])
    monkeypatch.setattr(profiles, "get_valid_access_token", tokens)
    sent: list[str] = []

    async def request(client: httpx2.AsyncClient, method: str, url: str, **_kwargs: object):
        sent.append(client.headers["Authorization"])
        status = 401 if len(sent) == 1 else 200
        return httpx2.Response(status, json={}, request=httpx2.Request(method, url))

    monkeypatch.setattr(github_http, "github_request", request)
    async with GitHubClient.as_user("octocat") as github:
        assert await github.get("user") == {}
    assert sent == ["Bearer stale", "Bearer fresh"]
    tokens.assert_awaited_with("octocat", force_refresh=True)


async def test_as_user_without_a_usable_token_asks_for_sign_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(profiles, "get_valid_access_token", AsyncMock(return_value=None))
    with pytest.raises(GitHubSignInRequired):
        async with GitHubClient.as_user("octocat"):
            pass


async def test_as_user_asks_for_sign_in_when_github_rejects_the_refreshed_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tokens = AsyncMock(side_effect=["stale", "still-rejected"])
    monkeypatch.setattr(profiles, "get_valid_access_token", tokens)

    async def request(_client: httpx2.AsyncClient, method: str, url: str, **_kwargs: object):
        return httpx2.Response(
            401, json={"message": "Bad credentials"}, request=httpx2.Request(method, url)
        )

    monkeypatch.setattr(github_http, "github_request", request)
    async with GitHubClient.as_user("octocat") as github:
        with pytest.raises(GitHubSignInRequired):
            await asyncio.gather(github.get("user"), github.get("user/repos"))
    assert tokens.await_count == 2


@pytest.mark.parametrize("conclusion", ["cancelled", "stale", "failure"])
@pytest.mark.parametrize("replacement", ["success", "failure", None])
async def test_check_runs_only_discards_superseded_cancellations(
    monkeypatch: pytest.MonkeyPatch, conclusion: str, replacement: str | None
) -> None:
    runs = [
        {
            "name": "validate README",
            "status": "completed" if result else "in_progress",
            "conclusion": result,
            "app": {"id": 15368, "slug": "github-actions"},
            "check_suite": {"id": suite},
        }
        for suite, result in [(10, conclusion), (20, replacement)]
    ]
    workflows = [
        {"id": suite, "check_suite_id": suite, "workflow_id": 100, "event": "pull_request"}
        for suite in (10, 20)
    ]
    pages = AsyncMock(side_effect=[runs, workflows])
    monkeypatch.setattr(github_http.RepoClient, "pages", pages)
    async with httpx2.AsyncClient() as http:
        result = await GitHubClient(http).repo("acme", "app").check_runs("a" * 40)
    assert result == (runs if conclusion == "failure" else [runs[1]])


@pytest.mark.parametrize(
    "distinct", ["workflow", "app", "event", "same_run", "older_success", "unavailable"]
)
async def test_check_runs_retains_cancellations_without_a_known_replacement(
    monkeypatch: pytest.MonkeyPatch, distinct: str
) -> None:
    runs = [
        {
            "name": "test",
            "status": "completed",
            "conclusion": result,
            "app": {"id": 15368, "slug": "github-actions"},
            "check_suite": {"id": suite},
        }
        for suite, result in [(10, "cancelled"), (20, "success")]
    ]
    workflows = [
        {"id": suite, "check_suite_id": suite, "workflow_id": 100, "event": "pull_request"}
        for suite in (10, 20)
    ]
    if distinct == "workflow":
        workflows[1]["workflow_id"] = 200
    elif distinct == "app":
        runs[1]["app"] = {"id": 123, "slug": "other-app"}
    elif distinct == "event":
        workflows[1]["event"] = "push"
    elif distinct == "same_run":
        runs[1]["check_suite"] = {"id": 10}
    elif distinct == "older_success":
        workflows[1]["id"] = 5
    pages = AsyncMock(
        side_effect=[
            runs,
            ValueError("Actions unavailable") if distinct == "unavailable" else workflows,
        ]
    )
    monkeypatch.setattr(github_http.RepoClient, "pages", pages)
    async with httpx2.AsyncClient() as http:
        assert await GitHubClient(http).repo("acme", "app").check_runs("a" * 40) == runs
