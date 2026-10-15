from datetime import UTC, datetime, timedelta

import pytest
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


class _Runtimes:
    def __init__(self, statuses: dict[str, ThreadStatus]) -> None:
        self.runtime = _Runtime(statuses)

    def runs_on(self, _runtime: Runtime) -> _Runtime:
        return self.runtime


async def _busy_since(thread_id: str, since: datetime) -> None:
    await index.create(thread_id, runtime="mda", status="busy")
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
