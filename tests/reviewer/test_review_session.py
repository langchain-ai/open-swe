from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agent.threads import listing, summary


def _review_thread(**metadata: object) -> dict[str, object]:
    return {
        "thread_id": "review-thread",
        "status": "idle",
        "metadata": {
            "source": "review_chat",
            "kind": "review_chat",
            "github_login": "alice",
            "repo_owner": "acme",
            "repo_name": "api",
            "pr_number": 7,
            "pr_url": "https://github.com/acme/api/pull/7",
            "title": "Add retries",
            "walkthrough_requested_at_ms": 1_000,
            **metadata,
        },
    }


def _client(runs: list[dict[str, str]] | Exception) -> SimpleNamespace:
    runs_list = (
        AsyncMock(side_effect=runs) if isinstance(runs, Exception) else AsyncMock(return_value=runs)
    )
    return SimpleNamespace(
        threads=SimpleNamespace(update=AsyncMock()),
        runs=SimpleNamespace(list=runs_list),
    )


def test_review_chat_is_readable_only_by_its_owner():
    metadata = _review_thread()["metadata"]
    assert summary.thread_is_readable(metadata, "Alice")
    assert not summary.thread_is_readable(metadata, "bob")
    assert not summary.thread_is_readable(metadata, None)
    assert not summary.thread_is_promptable(metadata, "alice")


@pytest.mark.asyncio
async def test_building_walkthrough_lists_as_running_review_page(monkeypatch):
    monkeypatch.setattr(summary, "get_langsmith_trace_url", AsyncMock(return_value=None))
    item = await summary._thread_summary(_review_thread(walkthrough_state="building"))
    assert item["status"] == "running"
    assert item["reviewPage"] == {"owner": "acme", "repo": "api", "number": 7}


@pytest.mark.asyncio
async def test_ready_walkthrough_stays_unread_until_viewed_after_it(monkeypatch):
    monkeypatch.setattr(summary, "get_langsmith_trace_url", AsyncMock(return_value=None))
    unseen = await summary._thread_summary(
        _review_thread(
            walkthrough_state="ready", walkthrough_ready_at_ms=5_000, last_viewed_at_ms=2_000
        )
    )
    seen = await summary._thread_summary(
        _review_thread(
            walkthrough_state="ready", walkthrough_ready_at_ms=5_000, last_viewed_at_ms=6_000
        )
    )
    assert (unseen["status"], unseen["viewed"]) == ("finished", False)
    assert seen["viewed"] is True


@pytest.mark.asyncio
async def test_settle_waits_while_the_scout_runs(monkeypatch):
    client = _client([{"status": "running"}])
    thread = _review_thread(walkthrough_state="building")
    assert await listing.settle_review_walkthrough(client, thread) is thread
    client.threads.update.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(("stored", "state"), [(True, "ready"), (False, "failed")])
async def test_settle_records_the_scout_outcome(monkeypatch, stored, state):
    monkeypatch.setattr(listing.Walkthrough, "generated_since", AsyncMock(return_value=stored))
    client = _client([{"status": "success"}])
    settled = await listing.settle_review_walkthrough(
        client, _review_thread(walkthrough_state="building")
    )
    assert settled["metadata"]["walkthrough_state"] == state
    assert (settled["metadata"]["walkthrough_ready_at_ms"] is not None) is stored
    client.threads.update.assert_awaited_once()


@pytest.mark.asyncio
async def test_settle_keeps_building_when_scout_runs_are_unreadable():
    client = _client(RuntimeError("langgraph down"))
    thread = _review_thread(walkthrough_state="building")
    assert await listing.settle_review_walkthrough(client, thread) is thread
    client.threads.update.assert_not_awaited()


@pytest.mark.asyncio
async def test_listing_hides_other_users_review_chats():
    thread = _review_thread()
    client = SimpleNamespace(
        threads=SimpleNamespace(search=AsyncMock(side_effect=[[thread], [thread]]))
    )
    mine = await listing._collect_thread_candidates(client, [{}], viewer_login="alice")
    theirs = await listing._collect_thread_candidates(client, [{}], viewer_login="bob")
    assert [t["thread_id"] for t in mine] == ["review-thread"]
    assert theirs == []
