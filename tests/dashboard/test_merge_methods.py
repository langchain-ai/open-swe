from unittest.mock import AsyncMock, MagicMock

import httpx2
import pytest

from agent.github import http as github_http
from agent.github import repo_merge_methods as merge_methods


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
    monkeypatch.setattr(github_http, "github_request", request)
    repo = github_http.GitHubClient(MagicMock()).repo("acme", "app")
    result = await merge_methods.repository_merge_methods(repo)
    assert result.merge_methods == expected
    assert result.model_dump(by_alias=True, mode="json") == {"mergeMethods": expected}
    assert request.await_args.args[1:] == ("GET", "https://api.github.com/repos/acme/app")
