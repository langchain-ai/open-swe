from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openswe.threads import listing, summary


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
async def test_failed_walkthrough_is_an_error_after_a_successful_chat_run(monkeypatch):
    monkeypatch.setattr(summary, "get_langsmith_trace_url", AsyncMock(return_value=None))
    item = await summary._thread_summary(
        _review_thread(walkthrough_state="failed", latest_run_status="success")
    )
    assert item["status"] == "error"


@pytest.mark.asyncio
async def test_settle_keeps_building_when_scout_runs_are_unreadable():
    client = _client(RuntimeError("langgraph down"))
    thread = _review_thread(walkthrough_state="building")
    assert await listing.settle_review_walkthrough(client, thread) is thread
    client.threads.update.assert_not_awaited()
