from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from openswe.database.postgres import transaction
from openswe.threads import index, status
from openswe.threads.index import Runtime, ThreadStatus
from openswe.ui_invalidations import Topic, outbox

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


@pytest.mark.parametrize("finished", ["idle", "error", "interrupted"])
async def test_reviewer_status_reconciliation_refreshes_its_open_review_page(
    monkeypatch: pytest.MonkeyPatch, finished: ThreadStatus
) -> None:
    await _busy_since("reviewer", datetime.now(UTC) - timedelta(hours=1))
    await index.update(
        "reviewer",
        {"kind": "reviewer", "pr": {"owner": "Acme", "name": "Widgets", "number": 42}},
    )
    runtime = _Runtimes({"reviewer": "busy"})
    monkeypatch.setattr(status, "RUNTIMES", runtime)
    topic = "pull-requests/acme/widgets/42"

    await status.refresh_thread_status("reviewer")
    assert await outbox.invalidated_since({topic: 60}) == set()

    runtime.runtime.statuses["reviewer"] = finished
    assert await status.reconcile_busy_threads() == 1
    assert await outbox.invalidated_since({topic: 60, Topic.PULL_REQUESTS.name: 60}) == {
        topic,
        Topic.PULL_REQUESTS.name,
    }
    async with transaction() as conn:
        await conn.execute(text("DELETE FROM ui_invalidation"))

    await status.refresh_thread_status("reviewer")
    assert await outbox.invalidated_since({topic: 60}) == set()
