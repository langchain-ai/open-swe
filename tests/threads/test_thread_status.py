from datetime import UTC, datetime, timedelta

import pytest
from pydantic import JsonValue
from sqlalchemy import text

from openswe.database.postgres import transaction
from openswe.threads import index, status
from openswe.threads.index import Runtime, ThreadStatus

pytestmark = pytest.mark.usefixtures("registry_db")


class _Runtime:
    def __init__(self, statuses: dict[str, ThreadStatus]) -> None:
        self.statuses = statuses

    async def status(self, thread_id: str) -> ThreadStatus | None:
        return self.statuses.get(thread_id)

    async def indexed(self, thread_id: str) -> index.IndexedThread | None:
        known = self.statuses.get(thread_id)
        if known is None:
            return None
        now = datetime.now(UTC)
        return index.IndexedThread(
            thread_id=thread_id,
            runtime="mda",
            metadata={},
            status=known,
            created_at=now,
            updated_at=now,
        )


class _Runtimes:
    def __init__(self, statuses: dict[str, ThreadStatus]) -> None:
        self.runtime = _Runtime(statuses)

    def runs_on(self, _runtime: Runtime) -> _Runtime:
        return self.runtime


async def _busy_since(
    thread_id: str, since: datetime, metadata: dict[str, JsonValue] | None = None
) -> None:
    await index.create(thread_id, runtime="mda", status="busy", metadata=metadata or {})
    async with transaction() as conn:
        await conn.execute(
            text("UPDATE thread_index SET status_since = :since WHERE thread_id = :thread_id"),
            {"since": since, "thread_id": thread_id},
        )


async def test_threads_stuck_busy_are_settled_from_their_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    long_ago = datetime.now(UTC) - timedelta(hours=1)
    await _busy_since("finished", long_ago)
    await _busy_since("still-running", long_ago)
    await _busy_since("just-started", datetime.now(UTC))
    await _busy_since("gone-from-runtime", long_ago)
    monkeypatch.setattr(
        status,
        "RUNTIMES",
        _Runtimes(
            {
                "finished": "interrupted",
                "still-running": "busy",
                "just-started": "idle",
                "gone-from-runtime": "idle",
            }
        ),
    )

    assert await status.reconcile_busy_threads() == 2

    settled = {
        thread_id: (await index.get(thread_id))
        for thread_id in ("finished", "still-running", "just-started", "gone-from-runtime")
    }
    assert {thread_id: thread.status for thread_id, thread in settled.items() if thread} == {
        "finished": "interrupted",
        "still-running": "busy",
        "just-started": "busy",
        "gone-from-runtime": "idle",
    }


async def test_a_channel_thread_mda_says_nothing_about_stays_busy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    long_ago = datetime.now(UTC) - timedelta(hours=1)
    await _busy_since("hidden", long_ago, metadata={"channel": "linear"})
    await _busy_since("shown", long_ago, metadata={"channel": "linear"})
    monkeypatch.setattr(status, "RUNTIMES", _Runtimes({"shown": "idle"}))

    assert await status.reconcile_busy_threads() == 1

    hidden = await index.get("hidden")
    shown = await index.get("shown")
    assert hidden is not None and hidden.status == "busy"
    assert shown is not None and shown.status == "idle"
