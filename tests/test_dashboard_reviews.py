from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from agent.dashboard import review_api, routes
from agent.reviewer_findings import REVIEWER_THREAD_KIND


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
async def test_list_reviews_pushes_author_into_metadata_filter(monkeypatch) -> None:
    client, captured = _fake_client([[]])
    monkeypatch.setattr(review_api, "langgraph_client", lambda: client)

    reviews, has_more = await review_api.list_reviews(20, author="octocat")

    assert captured["calls"][0]["metadata"] == {
        "kind": REVIEWER_THREAD_KIND,
        "pr": {"author": "octocat"},
    }
    assert reviews == []
    assert has_more is False


@pytest.mark.asyncio
async def test_list_reviews_no_author_filters_kind_only(monkeypatch) -> None:
    client, captured = _fake_client([[]])
    monkeypatch.setattr(review_api, "langgraph_client", lambda: client)

    await review_api.list_reviews(20, author=None)

    assert captured["calls"][0]["metadata"] == {"kind": REVIEWER_THREAD_KIND}


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
async def test_accessible_repo_full_names_lowercases(monkeypatch) -> None:
    fetch = AsyncMock(return_value=([], [{"full_name": "Acme/Repo"}, {"full_name": "Acme/Other"}]))
    monkeypatch.setattr(routes, "_fetch_user_installations_and_repos", fetch)

    names = await routes.accessible_repo_full_names("octocat")

    assert names == frozenset({"acme/repo", "acme/other"})


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
    monkeypatch.setattr(routes, "_fetch_user_installations_and_repos", fetch)

    first = await routes.accessible_repo_full_names("octocat")
    second = await routes.accessible_repo_full_names("octocat")

    assert first == frozenset({"acme/repo"})
    assert second == frozenset()
    assert fetch.await_count == 2
