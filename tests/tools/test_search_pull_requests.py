from contextlib import asynccontextmanager
from importlib import import_module
from unittest.mock import AsyncMock

import httpx2
import pytest

from agent.github.sandbox_access import SandboxGitHubAccess
from agent.run_config import Repo, RunConfig
from agent.tools.errors import ToolError

search = import_module("agent.tools.search_pull_requests")


@pytest.fixture
def context(monkeypatch):
    monkeypatch.setattr(
        search.RunConfig,
        "from_runtime",
        lambda: RunConfig(
            thread_id="thread", workspace="default", repo=Repo(owner="acme", name="app")
        ),
    )
    monkeypatch.setattr(search, "thread_token_repositories", AsyncMock(return_value=None))
    token = AsyncMock(return_value=SandboxGitHubAccess("repo-token"))
    monkeypatch.setattr(search, "workspace_token", token)
    return token


async def test_search_text_pagination_and_repo_isolation(monkeypatch, context):
    def handle(request):
        assert request.headers["Authorization"] == "Bearer repo-token"
        assert request.headers["Accept"] == "application/vnd.github.text-match+json"
        assert (
            request.url.params["q"]
            == '"needle" repo:acme/other is:pr repo:acme/app in:title,body,comments'
        )
        assert request.url.params["page"] == "2"
        return httpx2.Response(
            200,
            json={
                "total_count": 41,
                "incomplete_results": True,
                "items": [
                    {
                        "number": number,
                        "title": "Matches in a comment",
                        "repository_url": f"https://api.github.com/repos/acme/{repo}",
                        "state": "closed",
                        "user": {"login": "alice"},
                        "body": "x" * 4001,
                        "updated_at": "2026-01-01T00:00:00Z",
                        "pull_request": {} if is_pr else None,
                        "text_matches": [{"fragment": "needle in a comment"}],
                    }
                    for number, repo, is_pr in [
                        (1, "app", True),
                        (2, "other", True),
                        (3, "app", False),
                    ]
                ],
            },
        )

    @asynccontextmanager
    async def client(**kwargs):
        async with httpx2.AsyncClient(
            headers={"Authorization": f"Bearer {kwargs['token']}", **kwargs["headers"]},
            transport=httpx2.MockTransport(handle),
        ) as session:
            yield session

    monkeypatch.setattr(search, "github_client", client)
    result = await search.search_pull_requests('"needle" repo:acme/other', page=2)
    context.assert_awaited_once_with(
        "default", repositories=["acme/app"], permissions={"pull_requests": "read"}
    )
    assert result["success"] is True
    assert result["incomplete_results"] is True
    assert result["next_page"] == 3
    assert result["results"] == [
        {
            "number": 1,
            "url": "https://github.com/acme/app/pull/1",
            "title": "Matches in a comment",
            "state": "closed",
            "draft": False,
            "author": "alice",
            "updated_at": "2026-01-01T00:00:00Z",
            "body": "x" * 4000,
            "body_truncated": True,
            "fragments": ["needle in a comment"],
        }
    ]


async def test_search_cannot_widen_public_event_thread_access(monkeypatch, context):
    monkeypatch.setattr(search, "thread_token_repositories", AsyncMock(return_value=["acme/app"]))
    with pytest.raises(ToolError):
        await search.search_pull_requests("needle", repo="acme/private")
    context.assert_not_awaited()


async def test_invalid_search_response_is_not_reported_as_no_matches(monkeypatch, context):
    @asynccontextmanager
    async def client(**kwargs):
        async with httpx2.AsyncClient(
            transport=httpx2.MockTransport(lambda request: httpx2.Response(200, json={}))
        ) as session:
            yield session

    monkeypatch.setattr(search, "github_client", client)
    with pytest.raises(ToolError) as raised:
        await search.search_pull_requests("needle")
    assert "results" not in raised.value.details
