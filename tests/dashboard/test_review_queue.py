from typing import Any

import pytest
from fastapi import HTTPException

import agent.dashboard.review_queue as review_queue
from agent.dashboard.review_queue import (
    get_review_queue,
    get_review_queue_repos,
    set_review_queue_repos,
)
from tests.conftest import FakeStore


def _pull(
    repo: str,
    number: int,
    *,
    mergeable: str = "MERGEABLE",
    rollup: str | None = "SUCCESS",
    has_rollup: bool = True,
    is_draft: bool = False,
    review_decision: str | None = None,
    author: str | None = "someone",
) -> dict[str, Any]:
    return {
        "number": number,
        "title": f"{repo}#{number}",
        "url": f"https://github.com/{repo}/pull/{number}",
        "isDraft": is_draft,
        "mergeable": mergeable,
        "reviewDecision": review_decision,
        "updatedAt": "2026-01-01T00:00:00Z",
        "additions": number * 10,
        "deletions": number,
        "changedFiles": number + 1,
        "author": {"login": author} if author else None,
        "repository": {"nameWithOwner": repo},
        "commits": {
            "nodes": [{"commit": {"statusCheckRollup": {"state": rollup} if has_rollup else None}}]
        },
    }


class _Response:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._payload


def _patch_github(
    monkeypatch: pytest.MonkeyPatch, payload: dict[str, Any], calls: list[dict[str, Any]]
) -> None:
    class _Client:
        async def __aenter__(self) -> _Client:
            return self

        async def __aexit__(self, *_: object) -> bool:
            return False

    def client(**_kwargs: object) -> _Client:
        return _Client()

    async def request(_client: object, _method: str, _url: str, *, json: Any, **_kwargs: object):
        calls.append(json)
        return _Response(payload)

    monkeypatch.setattr(review_queue, "github_client", client)
    monkeypatch.setattr(review_queue, "github_request", request)


@pytest.fixture(autouse=True)
def _clear_cache_and_reviews(monkeypatch: pytest.MonkeyPatch) -> None:
    review_queue._cache.clear()

    async def no_review(_owner: str, _repo: str, _number: int) -> None:
        return None

    monkeypatch.setattr(review_queue, "review_summary_for_pull", no_review)
    monkeypatch.setattr(review_queue, "get_valid_access_token", _token)


async def _token(_login: str) -> str:
    return "gho_test"


async def test_keeps_only_ready_pulls_and_maps_fields(
    monkeypatch: pytest.MonkeyPatch, fake_store: FakeStore
) -> None:
    await set_review_queue_repos("octocat", ["acme/alpha", "acme/beta"])
    calls: list[dict[str, Any]] = []
    _patch_github(
        monkeypatch,
        {
            "data": {
                "search": {
                    "nodes": [
                        _pull("acme/alpha", 1, review_decision="REVIEW_REQUIRED"),
                        _pull("acme/beta", 2, has_rollup=False, author=None),
                        {},
                        _pull("acme/alpha", 3, is_draft=True),
                        _pull("acme/alpha", 4, mergeable="CONFLICTING"),
                        _pull("acme/alpha", 5, mergeable="UNKNOWN"),
                        _pull("acme/alpha", 6, rollup="FAILURE"),
                        _pull("acme/alpha", 7, rollup="PENDING"),
                    ]
                }
            }
        },
        calls,
    )

    payload = await get_review_queue("octocat")

    assert [(item.repo_full_name, item.number) for item in payload.items] == [
        ("acme/alpha", 1),
        ("acme/beta", 2),
    ]
    first, second = payload.items
    assert (first.owner, first.repo, first.author) == ("acme", "alpha", "someone")
    assert (first.additions, first.deletions, first.changed_files) == (10, 1, 2)
    assert first.review_decision == "REVIEW_REQUIRED"
    assert first.ai_review is None
    # A PR with no checks configured still counts as ready, and a missing author is null.
    assert second.author is None

    search = calls[0]["variables"]["q"]
    assert "repo:acme/alpha" in search
    assert "repo:acme/beta" in search
    assert "-author:@me" in search
    assert payload.repos == ["acme/alpha", "acme/beta"]


async def test_ai_review_summary_is_attached(
    monkeypatch: pytest.MonkeyPatch, fake_store: FakeStore
) -> None:
    await set_review_queue_repos("octocat", ["acme/alpha"])
    _patch_github(
        monkeypatch,
        {"data": {"search": {"nodes": [_pull("acme/alpha", 1)]}}},
        [],
    )

    async def summary(_owner: str, _repo: str, _number: int) -> dict[str, Any]:
        return {"status": "error", "counts": {"bugs": 2, "flags": 3, "open": 5}, "extra": "ignored"}

    monkeypatch.setattr(review_queue, "review_summary_for_pull", summary)

    payload = await get_review_queue("octocat")

    assert payload.items[0].ai_review is not None
    assert payload.items[0].ai_review.status == "error"
    assert payload.items[0].ai_review.counts.bugs == 2
    assert payload.items[0].ai_review.counts.flags == 3


async def test_empty_repo_list_skips_github(
    monkeypatch: pytest.MonkeyPatch, fake_store: FakeStore
) -> None:
    calls: list[dict[str, Any]] = []
    _patch_github(monkeypatch, {"data": {"search": {"nodes": [_pull("acme/alpha", 1)]}}}, calls)

    payload = await get_review_queue("octocat")

    assert (payload.repos, payload.items) == ([], [])
    assert not calls


async def test_github_errors_surface_as_bad_gateway(
    monkeypatch: pytest.MonkeyPatch, fake_store: FakeStore
) -> None:
    await set_review_queue_repos("octocat", ["acme/alpha"])
    _patch_github(monkeypatch, {"errors": [{"message": "nope"}]}, [])

    with pytest.raises(HTTPException) as exc:
        await get_review_queue("octocat")

    assert exc.value.status_code == 502
    assert not review_queue._cache


async def test_repos_are_normalized_deduped_and_sorted(fake_store: FakeStore) -> None:
    record = await set_review_queue_repos(
        "octocat",
        [
            "https://github.com/acme/zeta.git",
            " acme/Alpha ",
            "acme/alpha",
            "github.com/acme/beta/",
        ],
    )

    assert record.repos == ["acme/Alpha", "acme/beta", "acme/zeta"]
    assert (await get_review_queue_repos("octocat")).repos == record.repos


async def test_invalid_and_oversized_repo_lists_are_rejected(fake_store: FakeStore) -> None:
    for bad in ["not-a-repo", "acme/..", "acme/alpha/extra", ""]:
        with pytest.raises(HTTPException) as exc:
            await set_review_queue_repos("octocat", [bad])
        assert exc.value.status_code == 400

    with pytest.raises(HTTPException) as exc:
        await set_review_queue_repos("octocat", [f"acme/repo{i}" for i in range(51)])
    assert exc.value.status_code == 400
    assert (await get_review_queue_repos("octocat")).repos == []


async def test_github_result_is_cached_per_login(
    monkeypatch: pytest.MonkeyPatch, fake_store: FakeStore
) -> None:
    await set_review_queue_repos("octocat", ["acme/alpha"])
    await set_review_queue_repos("hubot", ["acme/alpha"])
    calls: list[dict[str, Any]] = []
    _patch_github(monkeypatch, {"data": {"search": {"nodes": [_pull("acme/alpha", 1)]}}}, calls)

    await get_review_queue("octocat")
    await get_review_queue("octocat")
    assert len(calls) == 1

    await get_review_queue("hubot")
    assert len(calls) == 2
