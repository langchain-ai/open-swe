from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import httpx2
import pytest
from fastapi import HTTPException

from agent.dashboard import routes
from agent.github import merge_pull_request as merges


def _client(expected_token: str):
    @asynccontextmanager
    async def client(**kwargs):
        assert kwargs == {"token": expected_token}
        yield object()

    return client


@pytest.mark.parametrize(
    "flags,expected",
    [
        ({}, ["squash", "merge", "rebase"]),
        (
            {"allow_squash_merge": True, "allow_merge_commit": True, "allow_rebase_merge": True},
            ["squash", "merge", "rebase"],
        ),
        (
            {"allow_squash_merge": True, "allow_merge_commit": False, "allow_rebase_merge": False},
            ["squash"],
        ),
        (
            {"allow_squash_merge": False, "allow_merge_commit": False, "allow_rebase_merge": True},
            ["rebase"],
        ),
        ({"allow_squash_merge": False, "allow_merge_commit": None}, ["merge", "rebase"]),
    ],
)
async def test_merge_methods_reflect_repository_flags(monkeypatch, flags, expected):
    request = AsyncMock(
        return_value=httpx2.Response(
            200, json=flags, request=httpx2.Request("GET", "https://api.github.com")
        )
    )
    monkeypatch.setattr(merges, "github_client", _client("user-token"))
    monkeypatch.setattr(merges, "github_request", request)
    assert await merges.repository_merge_methods("acme", "app", "user-token") == {
        "mergeMethods": expected
    }
    assert request.await_args.args[1:] == ("GET", "https://api.github.com/repos/acme/app")


async def test_merge_methods_network_failure_is_bad_gateway(monkeypatch):
    monkeypatch.setattr(merges, "github_client", _client("user-token"))
    monkeypatch.setattr(
        merges, "github_request", AsyncMock(side_effect=httpx2.ConnectError("boom"))
    )
    with pytest.raises(HTTPException, match="Could not load merge settings from GitHub") as error:
        await merges.repository_merge_methods("acme", "app", "user-token")
    assert error.value.status_code == 502


async def test_merge_methods_error_status_is_bad_gateway(monkeypatch):
    monkeypatch.setattr(merges, "github_client", _client("user-token"))
    monkeypatch.setattr(
        merges,
        "github_request",
        AsyncMock(
            return_value=httpx2.Response(
                404,
                json={"message": "Not Found"},
                request=httpx2.Request("GET", "https://api.github.com"),
            )
        ),
    )
    with pytest.raises(HTTPException) as error:
        await merges.repository_merge_methods("acme", "app", "user-token")
    assert error.value.status_code == 502


async def test_merge_methods_rejects_invalid_repository():
    with pytest.raises(HTTPException) as error:
        await merges.repository_merge_methods("acme", "..", "user-token")
    assert error.value.status_code == 422


async def test_merge_methods_without_user_token_never_calls_github(monkeypatch):
    monkeypatch.setattr(routes, "get_valid_access_token", AsyncMock(return_value=None))
    methods = AsyncMock()
    monkeypatch.setattr(routes, "repository_merge_methods", methods)
    with pytest.raises(HTTPException) as error:
        await routes.api_my_pull_request_merge_methods("acme", "app", {"sub": "octocat"})
    assert error.value.status_code == 401
    methods.assert_not_awaited()
