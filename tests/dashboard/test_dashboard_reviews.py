from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from openswe.github import repos
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
    monkeypatch.setattr(review_api, "_require_app_token", AsyncMock(return_value="tok"))
    monkeypatch.setattr(
        review_api, "_github_get", AsyncMock(side_effect=HTTPException(404, "not found on GitHub"))
    )

    with pytest.raises(HTTPException) as excinfo:
        await review_api.get_review("acme", "app", 7)

    assert excinfo.value.status_code == 404
