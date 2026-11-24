"""A send reads a thread as running only while its runtime says so."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from openswe.threads import proxy
from openswe.threads.index import IndexedThread, ThreadStatus


def _thread(status: ThreadStatus, run_status: str) -> IndexedThread:
    now = datetime.now(UTC)
    return IndexedThread(
        thread_id="t1",
        runtime="mda",
        metadata={"latest_run_status": run_status},
        status=status,
        created_at=now,
        updated_at=now,
    )


async def test_an_idle_thread_still_marked_pending_reads_its_run_from_the_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A machine's thread: the run ended and the webhook set the index idle, but no reader
    # refreshed the metadata, so its next message would be refused as sent mid-run.
    ended = _thread("idle", "success")
    refresh = AsyncMock(return_value=ended)
    monkeypatch.setattr(proxy.THREADS, "with_latest_run", refresh)

    current = await proxy.with_current_run(_thread("idle", "pending"))

    refresh.assert_awaited_once()
    assert current.metadata["latest_run_status"] == "success"


@pytest.mark.parametrize(
    ("status", "run_status"), [("busy", "running"), ("idle", "success"), ("error", "error")]
)
async def test_a_thread_whose_index_agrees_is_not_read_again(
    monkeypatch: pytest.MonkeyPatch, status: ThreadStatus, run_status: str
) -> None:
    refresh = AsyncMock()
    monkeypatch.setattr(proxy.THREADS, "with_latest_run", refresh)

    thread = _thread(status, run_status)
    assert await proxy.with_current_run(thread) is thread
    refresh.assert_not_awaited()
