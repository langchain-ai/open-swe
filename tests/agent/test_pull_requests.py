"""Unit tests for the pull request record and its thread/review links."""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest

from agent import pull_requests
from agent.pull_requests import PullRequest
from agent.repositories import Repository
from tests.conftest import FakeStore


@asynccontextmanager
async def _unlocked(*args, **kwargs):
    yield


@pytest.fixture(autouse=True)
def _unlocked_registry(fake_store: FakeStore, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pull_requests, "agent_thread_pr_state_lock", _unlocked)


def _pr() -> PullRequest:
    return PullRequest(owner="lc", repo="repo", number=7)


def _client_returning(*pages: list[dict[str, object]]) -> MagicMock:
    client = MagicMock()
    client.threads.search = AsyncMock(side_effect=[*pages, []])
    return client


@pytest.mark.asyncio
async def test_first_linked_thread_is_primary_and_later_ones_are_secondary() -> None:
    await _pr().link_thread("opener")
    saved = await _pr().link_thread("fixer")

    assert saved.primary_thread_id == "opener"
    assert saved.thread_ids == ["opener", "fixer"]


@pytest.mark.asyncio
async def test_relinking_a_thread_does_not_duplicate_or_promote_it() -> None:
    await _pr().link_thread("opener")
    await _pr().link_thread("fixer")
    saved = await _pr().link_thread("fixer")

    assert saved.thread_ids == ["opener", "fixer"]
    assert saved.primary_thread_id == "opener"


@pytest.mark.asyncio
async def test_save_leaves_fields_the_caller_did_not_set() -> None:
    await PullRequest(owner="lc", repo="repo", number=7, title="Add widget", author="ada").save()
    saved = await PullRequest(owner="lc", repo="repo", number=7, state="merged").save()

    assert (saved.state, saved.title, saved.author) == ("merged", "Add widget", "ada")


@pytest.mark.asyncio
async def test_saving_a_pull_request_registers_its_repository() -> None:
    await PullRequest(owner="LangChain-AI", repo="Open-SWE", number=7).save(repository_private=True)

    repository = await Repository.get("langchain-ai/open-swe")
    assert repository is not None
    assert (repository.full_name, repository.private) == ("LangChain-AI/Open-SWE", True)


@pytest.mark.asyncio
async def test_backfill_promotes_the_oldest_thread_and_skips_reviewer_threads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = "https://github.com/lc/repo/pull/7"
    client = _client_returning(
        [
            {"thread_id": "newer", "metadata": {"pr_url": url}, "created_at": "2026-02-01"},
            {"thread_id": "reviewer", "metadata": {"kind": "reviewer"}, "created_at": "2026-01-01"},
            {"thread_id": "older", "metadata": {"pr_url": url}, "created_at": "2026-01-15"},
        ]
    )
    monkeypatch.setattr(pull_requests, "langgraph_client", lambda: client)

    threads = await (await PullRequest.load("lc", "repo", 7)).linked_threads()

    assert threads == ["older", "newer"]
    stored = await PullRequest.get("lc", "repo", 7)
    assert stored is not None
    assert stored.primary_thread_id == "older"


@pytest.mark.asyncio
async def test_backfill_runs_once_and_later_reads_use_the_record(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = "https://github.com/lc/repo/pull/7"
    client = _client_returning([{"thread_id": "t1", "metadata": {"pr_url": url}}])
    monkeypatch.setattr(pull_requests, "langgraph_client", lambda: client)

    await (await PullRequest.load("lc", "repo", 7)).linked_threads()
    search_calls = client.threads.search.await_count
    await (await PullRequest.load("lc", "repo", 7)).linked_threads()

    assert client.threads.search.await_count == search_calls


@pytest.mark.asyncio
async def test_relinking_a_review_replaces_the_entry_with_the_same_github_id() -> None:
    await _pr().link_review(reviewer_thread_id="rev", github_review_id=11, finding_count=3)
    saved = await _pr().link_review(reviewer_thread_id="rev", github_review_id=11, finding_count=1)

    assert [review.github_review_id for review in saved.reviews] == [11]
    assert saved.reviews[0].finding_count == 1
    assert saved.reviews[0].url == "https://github.com/lc/repo/pull/7#pullrequestreview-11"
