from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import httpx2
import pytest

from openswe.github import app as github_app
from openswe.github import http as github_http
from openswe.github import repos
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
async def test_accessible_repo_full_names_resolves_fresh_each_call(monkeypatch) -> None:
    # Access is an authorization boundary, so it must not be cached across calls:
    # a second call re-fetches and reflects revoked access.
    fetch = AsyncMock(
        side_effect=[
            ([], [{"full_name": "acme/repo"}]),
            ([], []),
        ]
    )
    monkeypatch.setattr(repos, "fetch_user_installations_and_repos", fetch)

    first = await repos.accessible_repo_full_names("octocat")
    second = await repos.accessible_repo_full_names("octocat")

    assert first == frozenset({"acme/repo"})
    assert second == frozenset()
    assert fetch.await_count == 2


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
