from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import httpx2
import pytest

from openswe.github import app as github_app
from openswe.github import http as github_http
from openswe.github.http import GitHubError
from openswe.review import reviews as review_api
from openswe.review.findings import REVIEWER_THREAD_KIND

pytestmark = pytest.mark.usefixtures("findings_from_metadata")


def _thread(owner: str, name: str, number: int, author: str) -> dict[str, Any]:
    return {
        "thread_id": f"{owner}/{name}/{number}",
        "status": "idle",
        "updated_at": "2026-06-12T00:00:00Z",
        "metadata": {
            "kind": REVIEWER_THREAD_KIND,
            "pr": {"owner": owner, "name": name, "number": number, "author": author},
        },
    }


def _fake_client(pages: list[list[dict[str, Any]]]) -> tuple[Any, dict[str, Any]]:
    captured: dict[str, Any] = {"calls": []}
    queue = list(pages)

    async def search(**kwargs: Any) -> list[dict[str, Any]]:
        captured["calls"].append(kwargs)
        return queue.pop(0) if queue else []

    return SimpleNamespace(threads=SimpleNamespace(search=search)), captured


@pytest.mark.asyncio
async def test_list_reviews_applies_accessibility_and_has_more(monkeypatch) -> None:
    page = [
        _thread("acme", "a", 1, "octocat"),
        _thread("acme", "b", 2, "octocat"),
        _thread("acme", "a", 3, "octocat"),
    ]
    client, _ = _fake_client([page])
    monkeypatch.setattr(review_api, "langgraph_client", lambda: client)

    async def is_accessible(summary: dict[str, Any]) -> bool:
        return summary["full_name"] == "acme/a"

    reviews, has_more = await review_api.list_reviews(1, offset=0, is_accessible=is_accessible)

    assert [r["number"] for r in reviews] == [1]
    # two accessible records exist (1 and 3); page of 1 leaves more.
    assert has_more is True


@pytest.mark.asyncio
async def test_get_review_propagates_a_pull_request_missing_on_github(monkeypatch) -> None:
    monkeypatch.setattr(
        github_app, "get_github_app_installation_token", AsyncMock(return_value="t")
    )
    missing = httpx2.Response(
        404, json={"message": "Not Found"}, request=httpx2.Request("GET", "https://api.github.com")
    )
    monkeypatch.setattr(github_http, "github_request", AsyncMock(return_value=missing))

    with pytest.raises(GitHubError) as excinfo:
        await review_api.get_review("acme", "app", 7)

    assert excinfo.value.response.status_code == 404


@pytest.mark.asyncio
async def test_review_trace_uses_the_reviewer_thread(monkeypatch) -> None:
    trace_url = "https://smith.langchain.com/trace/reviewer"
    monkeypatch.setattr(
        github_app, "get_github_app_installation_token", AsyncMock(return_value="t")
    )
    monkeypatch.setattr(
        github_http,
        "github_request",
        AsyncMock(
            return_value=httpx2.Response(
                200,
                json={"title": "Example", "state": "open"},
                request=httpx2.Request("GET", "https://api.github.com"),
            )
        ),
    )
    thread = _thread("acme", "app", 7, "alice")
    monkeypatch.setattr(review_api, "_reviewer_thread_for", AsyncMock(return_value=thread))
    monkeypatch.setattr(review_api, "_scout_target", AsyncMock(return_value=None))
    trace = AsyncMock(return_value=trace_url)
    monkeypatch.setattr(review_api, "get_langsmith_trace_url", trace)

    detail = await review_api.get_review("acme", "app", 7)

    assert detail["trace_url"] == trace_url
    trace.assert_awaited_once_with("acme/app/7")
