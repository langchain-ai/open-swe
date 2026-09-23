from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from langgraph_sdk.errors import NotFoundError

from agent.github import repos
from agent.review import reviews as review_api
from agent.review.findings import REVIEWER_THREAD_KIND

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


async def test_review_summaries_preserve_counts_and_distinguish_missing(monkeypatch):
    thread = _thread("acme", "app", 1, "octocat")
    thread["metadata"]["findings"] = [
        {"id": "bug", "status": "open", "severity": "high", "confidence": "high"},
        {"id": "flag", "status": "open", "severity": "medium", "confidence": "medium"},
    ]
    client, captured = _fake_client([[thread], []])
    monkeypatch.setattr(review_api, "langgraph_client", lambda: client)
    result = await review_api.get_review_summaries([("acme", "app", 1), ("acme", "app", 2)])
    summary = result["acme/app#1"]
    assert summary is not None
    assert summary.counts.bugs == 1
    assert summary.counts.flags == 1
    assert result["acme/app#2"] is None
    assert captured["calls"][0]["metadata"]["pr"] == {"owner": "acme", "name": "app", "number": 1}


async def test_review_summary_json_keys_match_the_dashboard_client(monkeypatch):
    thread = _thread("acme", "app", 1, "octocat")
    client, _ = _fake_client([[thread]])
    monkeypatch.setattr(review_api, "langgraph_client", lambda: client)

    result = await review_api.get_review_summaries([("acme", "app", 1)])
    summary = result["acme/app#1"]
    assert summary is not None

    assert summary.model_dump(by_alias=True, mode="json") == review_api._thread_review_summary(
        thread, []
    )


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
    monkeypatch.setattr(repos, "fetch_user_installations_and_repos", fetch)

    names = await repos.accessible_repo_full_names("octocat")

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
    monkeypatch.setattr(repos, "fetch_user_installations_and_repos", fetch)

    first = await repos.accessible_repo_full_names("octocat")
    second = await repos.accessible_repo_full_names("octocat")

    assert first == frozenset({"acme/repo"})
    assert second == frozenset()
    assert fetch.await_count == 2


def _pr_payload() -> dict[str, Any]:
    return {
        "title": "Add widgets",
        "html_url": "https://github.com/acme/app/pull/7",
        "state": "open",
        "user": {"login": "octocat", "avatar_url": None},
        "head": {"sha": "f" * 40, "ref": "feature"},
        "base": {"ref": "main"},
        "updated_at": "2026-06-12T00:00:00Z",
        "additions": 3,
        "deletions": 1,
        "changed_files": 2,
        "commits": 1,
    }


@pytest.mark.asyncio
async def test_get_review_renders_a_pull_request_with_no_reviewer_thread(monkeypatch) -> None:
    async def missing_thread(_thread_id: str) -> dict[str, Any]:
        raise NotFoundError("no thread", response=SimpleNamespace(status_code=404), body=None)

    monkeypatch.setattr(
        review_api,
        "langgraph_client",
        lambda: SimpleNamespace(threads=SimpleNamespace(get=missing_thread)),
    )
    monkeypatch.setattr(review_api, "_require_app_token", AsyncMock(return_value="tok"))
    monkeypatch.setattr(review_api, "_github_get", AsyncMock(return_value=_pr_payload()))

    review = await review_api.get_review("acme", "app", 7)

    assert review["status"] == "none"
    assert review["thread_id"] is None
    assert review["findings"] == []
    assert review["walkthrough"] is None
    assert review["assessment"] is None
    assert review["pr"]["title"] == "Add widgets"
    assert review["head_sha"] == "f" * 40
    assert review["author"] == "octocat"


@pytest.mark.asyncio
async def test_get_review_propagates_a_pull_request_missing_on_github(monkeypatch) -> None:
    monkeypatch.setattr(review_api, "_require_app_token", AsyncMock(return_value="tok"))
    monkeypatch.setattr(
        review_api, "_github_get", AsyncMock(side_effect=HTTPException(404, "not found on GitHub"))
    )

    with pytest.raises(HTTPException) as excinfo:
        await review_api.get_review("acme", "app", 7)

    assert excinfo.value.status_code == 404
