from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import httpx2
import pytest
from fastapi import HTTPException

from agent.dashboard import routes
from agent.github import merge_pull_request as merges


@pytest.mark.parametrize("status,merged", [(200, True), (200, False), (409, False), (403, False)])
async def test_merge_requires_github_confirmation(monkeypatch, status, merged):
    @asynccontextmanager
    async def client(**kwargs):
        assert kwargs == {"token": "user-token"}
        yield object()

    request = AsyncMock(
        return_value=httpx2.Response(
            status,
            json={"merged": merged, "message": "Head changed"},
            request=httpx2.Request("PUT", "https://api.github.com"),
        )
    )
    monkeypatch.setattr(merges, "github_client", client)
    monkeypatch.setattr(merges, "github_request", request)
    body = merges.MergePullRequestRequest(sha="a" * 40, merge_method="squash")
    if status == 200 and merged:
        assert await merges.merge_pull_request("acme", "app", 1, body, "user-token") == {
            "merged": True
        }
    else:
        with pytest.raises(HTTPException, match="Head changed"):
            await merges.merge_pull_request("acme", "app", 1, body, "user-token")
    assert request.await_args.args[1:] == (
        "PUT",
        "https://api.github.com/repos/acme/app/pulls/1/merge",
    )
    assert request.await_args.kwargs == {
        "json": {"sha": "a" * 40, "merge_method": "squash"},
        "max_retries": 0,
    }


async def test_merge_without_user_token_never_calls_github(monkeypatch):
    monkeypatch.setattr(routes, "get_valid_access_token", AsyncMock(return_value=None))
    merge = AsyncMock()
    monkeypatch.setattr(routes, "merge_pull_request", merge)
    with pytest.raises(HTTPException) as error:
        await routes.api_merge_my_pull_request(
            "acme",
            "app",
            1,
            merges.MergePullRequestRequest(sha="a" * 40, merge_method="squash"),
            {"sub": "octocat"},
        )
    assert error.value.status_code == 401
    merge.assert_not_awaited()
