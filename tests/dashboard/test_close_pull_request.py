from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import httpx2
import pytest
from fastapi import HTTPException

from agent.github import close_pull_request as closes
from agent.github import pull_request_dashboard_routes as pr_routes


def _client(expected_token: str):
    @asynccontextmanager
    async def client(**kwargs):
        assert kwargs == {"token": expected_token}
        yield object()

    return client


def _response(status: int, payload: object) -> httpx2.Response:
    return httpx2.Response(
        status, json=payload, request=httpx2.Request("PATCH", "https://api.github.com")
    )


@pytest.mark.parametrize("status,state", [(200, "closed"), (200, "open"), (403, "open")])
async def test_close_requires_github_confirmation(monkeypatch, status, state):
    request = AsyncMock(
        return_value=_response(status, {"state": state, "message": "Not permitted"})
    )
    monkeypatch.setattr(closes, "github_client", _client("user-token"))
    monkeypatch.setattr(closes, "github_request", request)
    if status == 200 and state == "closed":
        result = await closes.close_pull_request("acme", "app", 7, "user-token")
        assert result == closes.ClosePullRequestResult(closed=True)
    else:
        with pytest.raises(HTTPException, match="Not permitted"):
            await closes.close_pull_request("acme", "app", 7, "user-token")
    assert request.await_args.args[1:] == (
        "PATCH",
        "https://api.github.com/repos/acme/app/pulls/7",
    )
    assert request.await_args.kwargs == {"json": {"state": "closed"}, "max_retries": 0}


async def test_close_network_failure_is_bad_gateway(monkeypatch):
    monkeypatch.setattr(closes, "github_client", _client("user-token"))
    monkeypatch.setattr(
        closes, "github_request", AsyncMock(side_effect=httpx2.ConnectError("boom"))
    )
    with pytest.raises(HTTPException) as error:
        await closes.close_pull_request("acme", "app", 7, "user-token")
    assert error.value.status_code == 502


async def test_close_rejects_invalid_identity():
    with pytest.raises(HTTPException) as error:
        await closes.close_pull_request("acme", "app", 0, "user-token")
    assert error.value.status_code == 422


async def test_close_without_user_token_never_calls_github(monkeypatch):
    monkeypatch.setattr(pr_routes, "get_valid_access_token", AsyncMock(return_value=None))
    close = AsyncMock()
    monkeypatch.setattr(pr_routes, "close_pull_request", close)
    with pytest.raises(HTTPException) as error:
        await pr_routes.api_close_my_pull_request("acme", "app", 7, {"sub": "octocat"})
    assert error.value.status_code == 401
    close.assert_not_awaited()
