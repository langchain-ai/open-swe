from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from pydantic import JsonValue
from sqlalchemy import text

from openswe import completion
from openswe.database.postgres import transaction
from openswe.threads import index, stalls, status
from openswe.threads.index import Runtime, ThreadStatus
from openswe.threads.runtimes import MdaRun, RunStatus

pytestmark = pytest.mark.usefixtures("registry_db")

_SLACK: dict[str, JsonValue] = {
    "source": "slack",
    "source_context": {"slack_thread": {"channel_id": "C1", "thread_ts": "123.45"}},
}


class _Mda:
    """MDA with frozen workers: a running run ignores its cancel; a pending one is dropped."""

    def __init__(self, runs: dict[str, list[MdaRun]], settled: dict[str, RunStatus]) -> None:
        self.runs = runs
        self.settled = settled
        self.cancelled: list[str] = []

    async def active_runs(self, thread_id: str) -> list[MdaRun]:
        runs = self.runs[thread_id]
        return [run for run in runs if run.status == "running" or run.run_id not in self.cancelled]

    async def run(self, thread_id: str, run_id: str) -> MdaRun | None:
        run = next(run for run in self.runs[thread_id] if run.run_id == run_id)
        ended = self.settled.get(run_id)
        return run.model_copy(update={"status": ended}) if ended else run

    async def cancel(self, thread_id: str, run_id: str) -> bool:
        self.cancelled.append(run_id)
        return True

    async def status(self, thread_id: str) -> ThreadStatus | None:
        return "busy"


class _Runtimes:
    def __init__(self, mda: _Mda) -> None:
        self.mda_runtime = mda

    def runs_on(self, _runtime: Runtime) -> _Mda:
        return self.mda_runtime


class _Threads:
    async def get(self, thread_id: str) -> dict[str, JsonValue]:
        return {"thread_id": thread_id, "metadata": _SLACK}

    async def update(self, *, thread_id: str, metadata: dict[str, JsonValue]) -> None:
        return None


class _Client:
    threads = _Threads()


async def _busy(thread_id: str, since: datetime) -> None:
    await index.create(thread_id, runtime="mda", status="busy", metadata=_SLACK)
    async with transaction() as conn:
        await conn.execute(
            text("UPDATE thread_index SET status_since = :since WHERE thread_id = :thread_id"),
            {"since": since, "thread_id": thread_id},
        )


async def test_a_run_that_stopped_responding_is_failed_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    long_ago = datetime.now(UTC) - timedelta(hours=3)
    for thread_id in ("frozen", "finished", "working"):
        await _busy(thread_id, long_ago)
    mda = _Mda(
        {
            thread_id: [MdaRun(run_id=f"run-{thread_id}", status="running", created_at=long_ago)]
            for thread_id in ("frozen", "finished", "working")
        },
        # Its real completion lands between the sweep finding it and failing it.
        settled={"run-finished": "success"},
    )
    activity = {"run-working": datetime.now(UTC) - timedelta(minutes=10)}

    async def last_run_activity(thread_id: str, run_id: str) -> datetime | None:
        return activity.get(run_id)

    runtimes = _Runtimes(mda)
    monkeypatch.setattr(stalls, "RUNTIMES", runtimes)
    monkeypatch.setattr(status, "RUNTIMES", runtimes)
    monkeypatch.setattr(stalls, "last_run_activity", last_run_activity)
    monkeypatch.setattr(completion, "langgraph_client", _Client)
    monkeypatch.setattr(completion, "get_langsmith_error_url", AsyncMock(return_value=None))
    reply = AsyncMock(return_value=True)
    monkeypatch.setattr(completion, "post_slack_thread_reply", reply)

    assert await stalls.fail_stalled_runs() == 1
    assert await stalls.fail_stalled_runs() == 0

    assert mda.cancelled == ["run-frozen"]
    statuses = {t: (await index.get(t)) for t in ("frozen", "finished", "working")}
    assert {t: thread.status for t, thread in statuses.items() if thread} == {
        "frozen": "error",
        "finished": "busy",
        "working": "busy",
    }
    # The platform's own failure webhook for the frozen run, arriving late.
    await completion.handle_run_completion(
        {"thread_id": "frozen", "run_id": "run-frozen", "status": "error"}
    )
    reply.assert_awaited_once()
    assert reply.await_args is not None
    assert "stopped responding" in reply.await_args.args[2]

    # The person resends; it queues behind the frozen run, which never let go.
    resent = datetime.now(UTC) - timedelta(hours=2)
    mda.runs["frozen"].append(MdaRun(run_id="run-resend", status="pending", created_at=resent))
    await index.set_status("frozen", "busy")
    async with transaction() as conn:
        await conn.execute(
            text("UPDATE thread_index SET status_since = :since WHERE thread_id = 'frozen'"),
            {"since": resent},
        )
    assert await stalls.fail_stalled_runs() == 1
    assert mda.cancelled == ["run-frozen", "run-resend"]
    assert reply.await_count == 2
    frozen = await index.get("frozen")
    assert frozen is not None and frozen.status == "error"
