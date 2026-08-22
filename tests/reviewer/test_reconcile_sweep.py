from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from support.langgraph_fakes import FakeLangGraphClient

from agent import reconcile


def _run(run_id: str, thread_id: str, age_seconds: float) -> dict[str, Any]:
    created = datetime.now(UTC) - timedelta(seconds=age_seconds)
    return {
        "run_id": run_id,
        "thread_id": thread_id,
        "status": "pending",
        "created_at": created.isoformat(),
    }


def _client(
    monkeypatch: pytest.MonkeyPatch,
    threads: list[dict[str, Any]],
    runs: dict[str, Any],
) -> FakeLangGraphClient:
    client = FakeLangGraphClient(threads=threads, runs=runs)
    monkeypatch.setattr(reconcile, "langgraph_client", lambda: client)
    return client


@pytest.mark.asyncio
async def test_cancels_only_stale_pending_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(
        monkeypatch,
        [{"thread_id": "t1"}],
        {
            "t1": [
                _run("old1", "t1", age_seconds=4000),
                _run("fresh1", "t1", age_seconds=60),
                _run("old2", "t1", age_seconds=10000),
            ]
        },
    )

    counts = await reconcile.reconcile_stale_runs(max_age_seconds=1800)

    assert counts == {"threads_checked": 1, "stale_runs": 2, "cancelled": 2}
    (cancel,) = client.runs.cancelled
    assert cancel["thread_id"] == "t1"
    assert sorted(cancel["run_ids"]) == ["old1", "old2"]


@pytest.mark.asyncio
async def test_no_stale_runs_means_no_cancel(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(
        monkeypatch, [{"thread_id": "t1"}], {"t1": [_run("fresh1", "t1", age_seconds=30)]}
    )

    counts = await reconcile.reconcile_stale_runs(max_age_seconds=1800)

    assert counts == {"threads_checked": 1, "stale_runs": 0, "cancelled": 0}
    assert client.runs.cancelled == []


@pytest.mark.asyncio
async def test_bad_thread_does_not_abort_sweep(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(
        monkeypatch,
        [{"thread_id": "bad"}, {"thread_id": "good"}],
        {
            "bad": RuntimeError("runs.list exploded"),
            "good": [_run("old1", "good", age_seconds=5000)],
        },
    )

    counts = await reconcile.reconcile_stale_runs(max_age_seconds=1800)

    # Both threads counted; the good thread is still reconciled despite the bad one.
    assert counts == {"threads_checked": 2, "stale_runs": 1, "cancelled": 1}
    (cancel,) = client.runs.cancelled
    assert cancel["thread_id"] == "good"
    assert cancel["run_ids"] == ["old1"]


@pytest.mark.asyncio
async def test_paginates_busy_threads(monkeypatch: pytest.MonkeyPatch) -> None:
    full_page = [{"thread_id": f"t{i}"} for i in range(reconcile._SEARCH_PAGE_SIZE)]
    runs_by_thread: dict[str, Any] = {thread["thread_id"]: [] for thread in full_page}
    runs_by_thread["tail"] = [_run("old", "tail", age_seconds=9000)]
    client = _client(monkeypatch, [*full_page, {"thread_id": "tail"}], runs_by_thread)

    counts = await reconcile.reconcile_stale_runs(max_age_seconds=1800)

    assert counts["threads_checked"] == reconcile._SEARCH_PAGE_SIZE + 1
    assert counts["cancelled"] == 1
    # Two search calls: first full page triggers a second page fetch.
    searches = client.threads.searches
    assert len(searches) == 2
    assert searches[0]["offset"] == 0
    assert searches[1]["offset"] == reconcile._SEARCH_PAGE_SIZE
    assert searches[0]["status"] == "busy"


@pytest.mark.asyncio
async def test_unparseable_created_at_is_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(
        monkeypatch,
        [{"thread_id": "t1"}],
        {
            "t1": [
                {
                    "run_id": "bad",
                    "thread_id": "t1",
                    "status": "pending",
                    "created_at": "not-a-date",
                },
                _run("old", "t1", age_seconds=5000),
            ]
        },
    )

    counts = await reconcile.reconcile_stale_runs(max_age_seconds=1800)

    assert counts == {"threads_checked": 1, "stale_runs": 1, "cancelled": 1}
    (cancel,) = client.runs.cancelled
    assert cancel["run_ids"] == ["old"]
