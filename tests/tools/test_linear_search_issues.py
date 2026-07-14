from __future__ import annotations

import importlib
from typing import Any

import pytest

from agent.utils import linear

linear_search_tool = importlib.import_module("agent.tools.linear_search_issues")


async def test_search_issues_returns_results_and_pagination(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    async def fake_graphql_request(
        query: str, variables: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        captured.update({"query": query, "variables": variables})
        return {
            "searchIssues": {
                "nodes": [
                    {
                        "id": "issue-id",
                        "identifier": "DCD-20",
                        "title": "User-message styling improvement",
                    }
                ],
                "totalCount": 12,
                "pageInfo": {"hasNextPage": True, "endCursor": "next-page"},
            }
        }

    monkeypatch.setattr(linear, "_graphql_request", fake_graphql_request)

    result = await linear.search_issues(
        "  user message styling  ",
        team_id="team-id",
        limit=5,
        include_archived=True,
        include_comments=True,
        after="current-page",
    )

    assert "searchIssues" in captured["query"]
    assert captured["variables"] == {
        "query": "user message styling",
        "filter": {"team": {"id": {"eq": "team-id"}}},
        "limit": 5,
        "includeArchived": True,
        "includeComments": True,
        "after": "current-page",
    }
    assert result == {
        "issues": [
            {
                "id": "issue-id",
                "identifier": "DCD-20",
                "title": "User-message styling improvement",
            }
        ],
        "total_count": 12,
        "page_info": {"hasNextPage": True, "endCursor": "next-page"},
    }


async def test_search_issues_rejects_blank_query(monkeypatch: pytest.MonkeyPatch) -> None:
    async def unexpected_request(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        pytest.fail("GraphQL request should not be made")

    monkeypatch.setattr(linear, "_graphql_request", unexpected_request)

    result = await linear.search_issues("   ")

    assert result == {"error": "Search query must not be empty"}


@pytest.mark.parametrize("limit", [0, 51])
async def test_search_issues_rejects_invalid_limit(
    monkeypatch: pytest.MonkeyPatch, limit: int
) -> None:
    async def unexpected_request(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        pytest.fail("GraphQL request should not be made")

    monkeypatch.setattr(linear, "_graphql_request", unexpected_request)

    result = await linear.search_issues("styling", limit=limit)

    assert result == {"error": "Search limit must be between 1 and 50"}


async def test_search_issues_propagates_graphql_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_graphql_request(
        _query: str, _variables: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        return {"error": "rate limited"}

    monkeypatch.setattr(linear, "_graphql_request", fake_graphql_request)

    assert await linear.search_issues("styling") == {"error": "rate limited"}


async def test_linear_search_issues_tool_delegates(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    async def fake_search_issues(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {"issues": []}

    monkeypatch.setattr(linear_search_tool, "search_issues", fake_search_issues)

    result = await linear_search_tool.linear_search_issues(
        "styling",
        team_id="team-id",
        limit=20,
        include_archived=True,
        include_comments=True,
        after="cursor",
    )

    assert result == {"issues": []}
    assert captured == {
        "query": "styling",
        "team_id": "team-id",
        "limit": 20,
        "include_archived": True,
        "include_comments": True,
        "after": "cursor",
    }
