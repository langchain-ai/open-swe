from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock

import pytest

from agent import reconcile


def _run(run_id: str, thread_id: str, age_seconds: float) -> dict[str, Any]:
    created = datetime.now(UTC) - timedelta(seconds=age_seconds)
    return {
        "run_id": run_id,
        "thread_id": thread_id,
        "status": "pending",
        "created_at": created.isoformat(),
    }


class _FakeThreads:
    def __init__(self, pages: list[list[dict[str, Any]]]) -> None:
        self._pages = pages
        self.search_calls: list[dict[str, Any]] = []

    async def search(self, **kwargs: Any) -> list[dict[str, Any]]:
        self.search_calls.append(kwargs)
        offset = kwargs.get("offset", 0)
        limit = kwargs.get("limit", 100)
        index = offset // limit if limit else 0
        if index < len(self._pages):
            return self._pages[index]
        return []


class _FakeRuns:
    def __init__(self, runs_by_thread: dict[str, Any]) -> None:
        self._runs_by_thread = runs_by_thread
        self.cancel_many = AsyncMock(return_value=None)
        self.list_calls: list[tuple[str, dict[str, Any]]] = []

    async def list(self, thread_id: str, **kwargs: Any) -> list[dict[str, Any]]:
        self.list_calls.append((thread_id, kwargs))
        value = self._runs_by_thread.get(thread_id, [])
        if isinstance(value, Exception):
            raise value
        return value


class _FakeClient:
    def __init__(self, threads: _FakeThreads, runs: _FakeRuns) -> None:
        self.threads = threads
        self.runs = runs


def _patch(monkeypatch: pytest.MonkeyPatch, client: _FakeClient) -> None:
    monkeypatch.setattr(reconcile, "langgraph_client", lambda: client)


@pytest.mark.asyncio
async def test_cancels_only_stale_pending_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    threads = _FakeThreads([[{"thread_id": "t1"}]])
    runs = _FakeRuns(
        {
            "t1": [
                _run("old1", "t1", age_seconds=4000),
                _run("fresh1", "t1", age_seconds=60),
                _run("old2", "t1", age_seconds=10000),
            ]
        }
    )
    _patch(monkeypatch, _FakeClient(threads, runs))

    counts = await reconcile.reconcile_stale_runs(max_age_seconds=1800)

    assert counts == {"threads_checked": 1, "stale_runs": 2, "cancelled": 2}
    runs.cancel_many.assert_awaited_once()
    kwargs = runs.cancel_many.await_args.kwargs
    assert kwargs["thread_id"] == "t1"
    assert sorted(kwargs["run_ids"]) == ["old1", "old2"]


@pytest.mark.asyncio
async def test_no_stale_runs_means_no_cancel(monkeypatch: pytest.MonkeyPatch) -> None:
    threads = _FakeThreads([[{"thread_id": "t1"}]])
    runs = _FakeRuns({"t1": [_run("fresh1", "t1", age_seconds=30)]})
    _patch(monkeypatch, _FakeClient(threads, runs))

    counts = await reconcile.reconcile_stale_runs(max_age_seconds=1800)

    assert counts == {"threads_checked": 1, "stale_runs": 0, "cancelled": 0}
    runs.cancel_many.assert_not_awaited()


@pytest.mark.asyncio
async def test_bad_thread_does_not_abort_sweep(monkeypatch: pytest.MonkeyPatch) -> None:
    threads = _FakeThreads([[{"thread_id": "bad"}, {"thread_id": "good"}]])
    runs = _FakeRuns(
        {
            "bad": RuntimeError("runs.list exploded"),
            "good": [_run("old1", "good", age_seconds=5000)],
        }
    )
    _patch(monkeypatch, _FakeClient(threads, runs))

    counts = await reconcile.reconcile_stale_runs(max_age_seconds=1800)

    # Both threads counted; the good thread is still reconciled despite the bad one.
    assert counts == {"threads_checked": 2, "stale_runs": 1, "cancelled": 1}
    runs.cancel_many.assert_awaited_once()
    assert runs.cancel_many.await_args.kwargs["thread_id"] == "good"
    assert runs.cancel_many.await_args.kwargs["run_ids"] == ["old1"]


@pytest.mark.asyncio
async def test_paginates_busy_threads(monkeypatch: pytest.MonkeyPatch) -> None:
    full_page = [{"thread_id": f"t{i}"} for i in range(reconcile._SEARCH_PAGE_SIZE)]
    second_page = [{"thread_id": "tail"}]
    threads = _FakeThreads([full_page, second_page])
    runs_by_thread: dict[str, Any] = {t["thread_id"]: [] for t in full_page}
    runs_by_thread["tail"] = [_run("old", "tail", age_seconds=9000)]
    runs = _FakeRuns(runs_by_thread)
    _patch(monkeypatch, _FakeClient(threads, runs))

    counts = await reconcile.reconcile_stale_runs(max_age_seconds=1800)

    assert counts["threads_checked"] == reconcile._SEARCH_PAGE_SIZE + 1
    assert counts["cancelled"] == 1
    # Two search calls: first full page triggers a second page fetch.
    assert len(threads.search_calls) == 2
    assert threads.search_calls[0]["offset"] == 0
    assert threads.search_calls[1]["offset"] == reconcile._SEARCH_PAGE_SIZE
    assert threads.search_calls[0]["status"] == "busy"


@pytest.mark.asyncio
async def test_unparseable_created_at_is_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    threads = _FakeThreads([[{"thread_id": "t1"}]])
    runs = _FakeRuns(
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
        }
    )
    _patch(monkeypatch, _FakeClient(threads, runs))

    counts = await reconcile.reconcile_stale_runs(max_age_seconds=1800)

    assert counts == {"threads_checked": 1, "stale_runs": 1, "cancelled": 1}
    assert runs.cancel_many.await_args.kwargs["run_ids"] == ["old"]
