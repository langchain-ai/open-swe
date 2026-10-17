from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from pydantic import JsonValue
from sqlalchemy import text

from openswe import completion
from openswe.database.postgres import transaction
from openswe.threads import index, status
from openswe.threads.index import Runtime, ThreadStatus

pytestmark = pytest.mark.usefixtures("registry_db")


class _Runtime:
    def __init__(
        self,
        statuses: dict[str, ThreadStatus],
        runs: dict[str, dict[str, JsonValue]] | None = None,
    ) -> None:
        self.statuses = statuses
        self.runs = runs or {}

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

    async def latest_run(self, thread_id: str) -> dict[str, JsonValue] | None:
        return self.runs.get(thread_id)

    async def get(self, thread_id: str) -> dict[str, JsonValue] | None:
        return {"thread_id": thread_id, "error": {"error": "RuntimeError", "message": "boom"}}


class _Runtimes:
    def __init__(
        self,
        statuses: dict[str, ThreadStatus],
        runs: dict[str, dict[str, JsonValue]] | None = None,
    ) -> None:
        self.runtime = _Runtime(statuses, runs)

    def runs_on(self, _runtime: Runtime) -> _Runtime:
        return self.runtime


async def _busy_since(
    thread_id: str,
    since: datetime,
    *,
    runtime: Runtime = "mda",
    metadata: dict[str, JsonValue] | None = None,
) -> None:
    await index.create(thread_id, runtime=runtime, status="busy", metadata=metadata or {})
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


async def test_ended_mda_runs_are_completed_as_their_webhook_or_hook_would(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    long_ago = datetime.now(UTC) - timedelta(hours=1)
    # A channel run that crashed: no webhook follows it, and it never reached after_agent.
    await _busy_since("channel", long_ago, metadata={"channel": "linear"})
    await _busy_since("own", long_ago)
    await _busy_since("running", long_ago)
    await _busy_since("local", long_ago, runtime="local")
    runs: dict[str, dict[str, JsonValue]] = {
        "channel": {"run_id": "run-c", "status": "error", "metadata": {"invocation_id": "i"}},
        "own": {"run_id": "run-o", "status": "success", "metadata": {}},
        "running": {"run_id": "run-r", "status": "running", "metadata": {}},
        "local": {"run_id": "run-l", "status": "success", "metadata": {}},
    }
    monkeypatch.setattr(
        status,
        "RUNTIMES",
        _Runtimes({"channel": "idle", "own": "idle", "running": "busy", "local": "idle"}, runs),
    )
    completed: list[dict[str, Any]] = []

    async def complete(payload: dict[str, Any], **_: object) -> dict[str, str]:
        completed.append(payload)
        return {"status": "ok"}

    monkeypatch.setattr(completion, "complete_run_once", complete)

    assert await status.reconcile_busy_threads() == 3

    assert sorted(completed, key=lambda payload: payload["thread_id"]) == [
        {
            "thread_id": "channel",
            "run_id": "run-c",
            "status": "error",
            # Every run on a channel's thread is the channel's: no failure reply, no follow-ups.
            "metadata": {"invocation_id": "i", "channel": "linear"},
            "error": {"error": "RuntimeError", "message": "boom"},
        },
        {"thread_id": "own", "run_id": "run-o", "status": "success", "metadata": {}},
    ]
    still_busy = await index.get("running")
    assert still_busy is not None and still_busy.status == "busy"


async def test_a_run_is_completed_once_whoever_reaches_it_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handled: list[str] = []

    async def handle(payload: dict[str, Any], **_: object) -> dict[str, str]:
        handled.append(str(payload["run_id"]))
        return {"status": "ok"}

    monkeypatch.setattr(completion, "handle_run_completion", handle)
    payload = {"thread_id": "t1", "run_id": "run-1", "status": "success"}

    assert await completion.complete_run_once(payload) == {"status": "ok"}
    # The webhook, the after_agent hook or the reconcile job arriving second does nothing.
    assert (await completion.complete_run_once(payload))["reason"] == "run already completed"
    assert handled == ["run-1"]


async def test_a_completion_that_failed_can_be_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fail(payload: dict[str, Any], **_: object) -> dict[str, str]:
        raise RuntimeError("database went away")

    monkeypatch.setattr(completion, "handle_run_completion", fail)
    payload = {"thread_id": "t1", "run_id": "run-2", "status": "success"}
    with pytest.raises(RuntimeError):
        await completion.complete_run_once(payload)

    async def handle(payload: dict[str, Any], **_: object) -> dict[str, str]:
        return {"status": "ok"}

    monkeypatch.setattr(completion, "handle_run_completion", handle)
    assert await completion.complete_run_once(payload) == {"status": "ok"}


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
