from datetime import UTC, datetime, timedelta
from uuid import uuid7

import pytest
from pydantic import JsonValue
from sqlalchemy import text

from openswe.database.postgres import read_only_transaction, transaction
from openswe.threads import index, status
from openswe.threads.index import Runtime, ThreadStatus
from openswe.threads.runtimes import MdaRun
from openswe.transcript.engine import Command, append
from openswe.transcript.events import MessageSender, ThreadCreated, TurnRequested, TurnStarted
from openswe.ui_invalidations import Topic, outbox

pytestmark = pytest.mark.usefixtures("registry_db")


class _Runtime:
    def __init__(self, statuses: dict[str, ThreadStatus]) -> None:
        self.statuses = statuses
        self.runs: dict[str, MdaRun] = {}
        self.errors: dict[str, str] = {}
        self.unreachable = False

    async def status(self, thread_id: str) -> ThreadStatus | None:
        return self.statuses.get(thread_id)

    async def latest_run(self, thread_id: str) -> MdaRun | None:
        return self.runs.get(thread_id)

    async def run_error(self, thread_id: str) -> str | None:
        if self.unreachable:
            raise TimeoutError("MDA did not answer")
        return self.errors.get(thread_id)

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


async def test_a_channel_thread_mda_says_nothing_about_stays_busy_until_its_run_never_came(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    long_ago = datetime.now(UTC) - timedelta(hours=1)
    await _busy_since("hidden", datetime.now(UTC) - timedelta(minutes=2), {"channel": "linear"})
    await _busy_since("never-started", long_ago, metadata={"channel": "linear"})
    await _busy_since("shown", long_ago, metadata={"channel": "linear"})
    monkeypatch.setattr(status, "RUNTIMES", _Runtimes({"shown": "idle"}))

    assert await status.reconcile_busy_threads() == 2

    statuses = {
        thread_id: thread.status
        for thread_id in ("hidden", "never-started", "shown")
        if (thread := await index.get(thread_id))
    }
    # MDA may not have created it yet; long after, its run never started.
    assert statuses == {"hidden": "busy", "never-started": "idle", "shown": "idle"}


async def test_a_run_that_failed_without_a_webhook_has_its_turn_failed_with_its_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A run that raises never reports its end, so only the reconcile can close its turn."""
    thread_id, turn_id = str(uuid7()), uuid7()
    owner = MessageSender(login="someone", kind="dashboard")
    await append(
        thread_id,
        [
            Command(
                command_id=f"thread:{thread_id}:created",
                event=ThreadCreated(title="A thread", source="dashboard", owner_login="someone"),
                actor_kind="user",
            ),
            Command(
                command_id=f"turn:{turn_id}:requested",
                event=TurnRequested(turn_id=turn_id, message_id="m-1", text="go", sender=owner),
                actor_kind="user",
                turn_id=turn_id,
            ),
            Command(
                command_id=f"turn:{turn_id}:started:run-1",
                event=TurnStarted(turn_id=turn_id, run_id="run-1"),
                actor_kind="agent",
                run_id="run-1",
                turn_id=turn_id,
            ),
        ],
    )
    await _busy_since(thread_id, datetime.now(UTC) - timedelta(hours=1))
    runtimes = _Runtimes({thread_id: "error"})
    runtimes.runtime.runs[thread_id] = MdaRun(
        run_id="run-1", status="error", created_at=datetime.now(UTC)
    )
    runtimes.runtime.errors[thread_id] = "BlockingError: Blocking call to os.getcwd"
    monkeypatch.setattr(status, "RUNTIMES", runtimes)

    # A turn that could not be settled keeps the thread busy, so the next pass retries it.
    runtimes.runtime.unreachable = True
    assert await status.reconcile_busy_threads() == 0
    assert (thread := await index.get(thread_id)) is not None and thread.status == "busy"

    runtimes.runtime.unreachable = False
    assert await status.reconcile_busy_threads() == 1

    async with read_only_transaction() as conn:
        turn = (
            await conn.execute(
                text("SELECT state, error FROM thread_turn WHERE turn_id = :turn_id"),
                {"turn_id": turn_id},
            )
        ).one()
    assert (turn.state, turn.error) == ("failed", "BlockingError: Blocking call to os.getcwd")


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
