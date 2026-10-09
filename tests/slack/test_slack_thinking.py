import asyncio
from unittest.mock import AsyncMock, call
from uuid import uuid4

import httpx
import pytest

from openswe.slack import thinking as slack_thinking
from openswe.source_context import SourceContext
from openswe.tasks.store import SidebarTaskMembership, Task, TaskContext, TaskMembership


@pytest.fixture(autouse=True)
def immediate_status_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(slack_thinking, "_STATUS_RETRY_DELAYS", (0.0, 0.0))


def _event(method: str, data: dict, *, namespace: list[str] | None = None) -> dict:
    return {
        "type": "event",
        "event_id": "1-0",
        "method": method,
        "params": {"namespace": namespace or [], "timestamp": 1, "data": data},
    }


async def test_streams_sanitized_tool_steps(monkeypatch) -> None:
    historical = _event(
        "tools",
        {
            "event": "tool-started",
            "tool_call_id": "old-call",
            "tool_name": "execute",
            "input": {"command": "echo historical"},
        },
    )
    events = [
        historical,
        _event("lifecycle", {"event": "running"}),
        _event(
            "tools",
            {
                "event": "tool-started",
                "tool_call_id": "call-1",
                "tool_name": "execute",
                "input": {"command": "echo secret-token"},
            },
        ),
        _event("tools", {"event": "tool-finished", "tool_call_id": "call-1"}),
        _event("lifecycle", {"event": "completed"}),
    ]
    events[1]["event_id"] = "synth:run-1:lc||running"
    events[-1]["event_id"] = "synth:run-1:lc||completed"

    class ThreadStream:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            pass

        def subscribe(self, channels):
            assert channels == ["lifecycle", "tools"]

            async def iterator():
                for event in events:
                    yield event

            return iterator()

    client = AsyncMock()
    client.threads.stream = lambda *_args, **_kwargs: ThreadStream()
    start = AsyncMock(return_value="2.0")
    append = AsyncMock()
    stop = AsyncMock()
    monkeypatch.setattr(slack_thinking, "start_slack_stream", start)
    monkeypatch.setattr(slack_thinking, "append_slack_stream", append)
    monkeypatch.setattr(slack_thinking, "stop_slack_stream", stop)
    monkeypatch.setattr(slack_thinking, "store_slack_run_mapping", AsyncMock())

    await slack_thinking.stream_slack_thinking_steps(
        client=client,
        thread_id="thread-1",
        run_id="run-1",
        channel_id="C1",
        thread_ts="1.0",
        mapping_thread_ts="1.0",
        original_message_ts="1.1",
        recipient_user_id="U1",
        recipient_team_id="T1",
    )

    start.assert_awaited_once()
    stop.assert_awaited_once()
    assert stop.await_args is not None
    final_chunks = stop.await_args.args[2]
    serialized = str(final_chunks)
    assert "Running a development command" in serialized
    assert "echo secret-token" in serialized
    assert "echo historical" not in serialized
    assert final_chunks[-1]["status"] == "complete"
    assert final_chunks[-1]["output"] == "Completed"


async def test_a_run_queued_behind_another_is_followed_to_its_end(monkeypatch) -> None:
    earlier_run_ended = _event("lifecycle", {"event": "completed"})
    earlier_run_ended["event_id"] = "synth:run-0:lc||completed"
    running = _event("lifecycle", {"event": "running"})
    completed = _event("lifecycle", {"event": "completed"})
    running["event_id"] = "synth:run-1:lc||running"
    completed["event_id"] = "synth:run-1:lc||completed"
    # The SDK ends a subscription at the earlier run's end; the next one sees this run.
    subscriptions = [[earlier_run_ended], [running, completed]]

    class ThreadStream:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            pass

        def subscribe(self, _channels):
            events = subscriptions.pop(0)

            async def iterator():
                for event in events:
                    yield event

            return iterator()

    streams: list[ThreadStream] = []

    def open_stream(*_args, **_kwargs) -> ThreadStream:
        streams.append(ThreadStream())
        return streams[-1]

    client = AsyncMock()
    client.threads.stream = open_stream
    stop = AsyncMock()
    monkeypatch.setattr(slack_thinking, "start_slack_stream", AsyncMock(return_value="2.0"))
    monkeypatch.setattr(slack_thinking, "append_slack_stream", AsyncMock())
    monkeypatch.setattr(slack_thinking, "stop_slack_stream", stop)
    monkeypatch.setattr(slack_thinking, "store_slack_run_mapping", AsyncMock())

    await slack_thinking.stream_slack_thinking_steps(
        client=client,
        thread_id="thread-1",
        run_id="run-1",
        channel_id="C1",
        thread_ts="1.0",
        mapping_thread_ts="1.0",
        original_message_ts="1.1",
    )

    assert stop.await_args is not None
    assert stop.await_args.args[2][-1]["status"] == "complete"
    assert "Interrupted" not in str(stop.await_args.args[2])
    assert len(streams) == 1
    client.runs.get.assert_not_awaited()


async def test_stop_sends_pending_updates_despite_append_backoff(monkeypatch) -> None:
    stop = AsyncMock()
    monkeypatch.setattr(slack_thinking, "stop_slack_stream", stop)
    stream = slack_thinking.SlackThinkingStream(
        client=AsyncMock(),
        thread_id="thread-1",
        run_id="run-1",
        channel_id="C1",
        thread_ts="0",
        recipient_user_id="U1",
        recipient_team_id="T1",
        mapping_thread_ts="0",
        original_message_ts="1.1",
    )
    stream.message_ts = "2.0"
    stream.retry_at = float("inf")
    step = slack_thinking.Step("step-1", "Reading", "in_progress")
    stream.steps[((), "call-1")] = step
    stream.pending[step.task_id] = step

    await stream.stop("success")

    stop.assert_awaited_once_with(
        "C1",
        "2.0",
        [
            {
                "type": "task_update",
                "id": "step-1",
                "title": "Reading",
                "status": "complete",
                "output": "Completed",
            }
        ],
    )
    assert not stream.pending


async def test_rate_limit_defers_append_until_retry_after(monkeypatch) -> None:
    clock = 10.0
    monkeypatch.setattr(slack_thinking, "monotonic", lambda: clock)
    append = AsyncMock(
        side_effect=[slack_thinking.SlackStreamError("rate_limited", retry_after=30), None]
    )
    monkeypatch.setattr(slack_thinking, "append_slack_stream", append)
    stream = slack_thinking.SlackThinkingStream(
        client=AsyncMock(),
        thread_id="thread-1",
        run_id="run-1",
        channel_id="C1",
        thread_ts="0",
        recipient_user_id="U1",
        recipient_team_id="T1",
        mapping_thread_ts="0",
        original_message_ts="1.1",
    )
    stream.message_ts = "2.0"
    step = slack_thinking.Step("step-1", "Reading", "in_progress")
    stream.pending[step.task_id] = step

    await stream.flush(force=True)
    clock = 39.0
    await stream.flush(force=True)
    assert append.await_count == 1

    clock = 40.0
    await stream.flush(force=True)
    assert append.await_count == 2
    assert not stream.pending


def _status_client() -> AsyncMock:
    client = AsyncMock()
    client.runs.join.return_value = {}
    client.runs.list.return_value = []
    client.threads.get.return_value = {"metadata": {}}
    return client


async def test_status_sync_does_not_clear_run_started_during_settlement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _status_client()
    client.runs.list.side_effect = [[], [], [{"run_id": "new-run"}]]
    set_status = AsyncMock()
    monkeypatch.setattr(slack_thinking, "set_slack_thread_status", set_status)
    await slack_thinking.sync_slack_background_status(
        client,
        "thread-1",
        metadata={"source_context": {"slack_thread": {"channel_id": "C1", "thread_ts": "1.0"}}},
    )
    client.threads.get.assert_not_awaited()
    set_status.assert_not_awaited()


@pytest.mark.parametrize("tool_name", ["slack_no_reply_needed", "slack_add_reaction"])
async def test_deferred_status_waits_for_work_decision(slack_api, tool_name: str) -> None:
    complete = asyncio.Event()
    client = _status_client()

    async def join(*_args):
        await complete.wait()
        return {}

    client.runs.join.side_effect = join
    loop = asyncio.get_running_loop()

    def respond(method, params, _headers):
        if method == "assistant.threads.setStatus" and params.get("status") == "Thinking...":
            loop.call_soon_threadsafe(complete.set)
        return 200, {"ok": True}, {}

    slack_api.handler = respond

    class ThreadStream:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            pass

        def subscribe(self, _channels):
            async def iterator():
                yield {
                    **_event("lifecycle", {"event": "running"}),
                    "event_id": "synth:previous-run:lc||running",
                }
                yield _event("tools", {"event": "tool-started", "tool_name": "execute"})
                yield {
                    **_event("lifecycle", {"event": "running"}),
                    "event_id": "synth:run-1:lc||running",
                }
                yield _event("tools", {"event": "tool-started", "tool_name": "ls"})
                assert not slack_api.calls
                yield _event("tools", {"event": "tool-started", "tool_name": tool_name})
                if tool_name == "slack_no_reply_needed":
                    complete.set()
                yield {
                    **_event("lifecycle", {"event": "completed"}),
                    "event_id": "synth:run-1:lc||completed",
                }

            return iterator()

    client.threads.stream = lambda *_args, **_kwargs: ThreadStream()
    async with asyncio.timeout(2):
        await slack_thinking.show_slack_thinking_status(
            client=client,
            thread_id="thread-1",
            run_id="run-1",
            channel_id="C1",
            thread_ts="1.0",
            defer_until_tool=True,
        )

    statuses = [params["status"] for method, params in slack_api.calls]
    assert statuses == (["Thinking...", ""] if tool_name == "slack_add_reaction" else [""])


async def test_status_wait_keeps_refreshing_after_repeated_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    refreshed = asyncio.Event()
    complete = asyncio.Event()
    statuses: list[str] = []
    client = _status_client()
    client.runs.list.return_value = [{"run_id": "run-1"}]

    async def join(_thread_id: str, _run_id: str) -> dict[str, object]:
        if client.runs.join.await_count <= 3:
            raise httpx.ConnectError("disconnected")
        await complete.wait()
        client.runs.list.return_value = []
        return {}

    async def set_status(_channel_id: str, _thread_ts: str, status: str) -> bool:
        statuses.append(status)
        if status == "Thinking..." and client.runs.join.await_count > 3:
            refreshed.set()
        return True

    client.runs.join.side_effect = join
    monkeypatch.setattr(slack_thinking, "set_slack_thread_status", set_status)
    monkeypatch.setattr(slack_thinking, "_STATUS_REFRESH_SECONDS", 0.0)
    monkeypatch.setattr(slack_thinking, "_DEFAULT_RETRY_SECONDS", 0.0)

    async with asyncio.timeout(2):
        async with asyncio.TaskGroup() as tasks:
            observer = tasks.create_task(
                slack_thinking.show_slack_thinking_status(
                    client=client,
                    thread_id="thread-1",
                    run_id="run-1",
                    channel_id="C1",
                    thread_ts="1.0",
                )
            )
            await refreshed.wait()
            assert not observer.done()
            assert set(statuses) == {"Thinking..."}
            complete.set()

    assert statuses[-1] == ""


@pytest.mark.parametrize("worker_wakeup", [False, True])
async def test_status_tracks_worker_runs_while_coordinator_idle(
    monkeypatch: pytest.MonkeyPatch, worker_wakeup: bool
) -> None:
    client = _status_client()
    coordinator_metadata = {
        "source_context": {"slack_thread": {"channel_id": "C1", "thread_ts": "1.0"}}
    }
    worker_metadata: dict[str, object] = {
        slack_thinking.RUNNING_BACKGROUND_TASKS_KEY: ["background-command"]
    }

    async def thread(thread_id: str) -> dict[str, object]:
        return {"metadata": worker_metadata if thread_id == "worker" else coordinator_metadata}

    client.threads.get.side_effect = thread
    task = Task(title="Work", workspace_id=uuid4(), coordinator_thread_id="coordinator")
    context = TaskContext(task, TaskMembership(thread_id="worker", task_id=task.id, role="worker"))
    monkeypatch.setattr(TaskMembership, "context_for_thread", AsyncMock(return_value=context))
    monkeypatch.setattr(
        slack_thinking,
        "sidebar_memberships",
        AsyncMock(
            return_value={
                "worker": SidebarTaskMembership(
                    thread_id="worker",
                    task_id=str(task.id),
                    role="worker",
                    coordinator_thread_id="coordinator",
                )
            }
        ),
    )
    worker_status = "pending"
    worker_done = asyncio.Event()

    async def join(_thread_id: str, _run_id: str) -> dict[str, object]:
        if worker_wakeup:
            await worker_done.wait()
        return {}

    client.runs.join.side_effect = join

    async def runs(thread_id: str, *, status: str, limit: int) -> list[dict[str, str]]:
        return (
            [{"run_id": "worker-run"}] if thread_id == "worker" and status == worker_status else []
        )

    client.runs.list.side_effect = runs
    statuses: list[str] = []
    refreshed = asyncio.Event()

    async def set_status(channel_id: str, thread_ts: str, status: str) -> bool:
        assert (channel_id, thread_ts) == ("C1", "1.0")
        statuses.append(status)
        if len(statuses) >= 3:
            refreshed.set()
        return True

    monkeypatch.setattr(slack_thinking, "set_slack_thread_status", set_status)
    monkeypatch.setattr(slack_thinking, "_STATUS_REFRESH_SECONDS", 0.001)
    async with asyncio.timeout(2):
        async with asyncio.TaskGroup() as tasks:
            if worker_wakeup:
                await slack_thinking.sync_slack_background_status(
                    client,
                    "worker",
                    resume=True,
                    run_id="worker-run",
                    source_context=SourceContext(),
                )
                observer = next(iter(slack_thinking._STATUS_OBSERVERS))
            else:
                observer = tasks.create_task(
                    slack_thinking.show_slack_thinking_status(
                        client=client,
                        thread_id="coordinator",
                        run_id="coordinator-run",
                        channel_id="C1",
                        thread_ts="1.0",
                    )
                )
            try:
                await refreshed.wait()
                assert not observer.done()
                assert set(statuses) == {"Thinking..."}
                worker_status = "running"
                await slack_thinking.clear_slack_thinking_status_if_idle(
                    client, "coordinator", "C1", "1.0"
                )
                assert set(statuses) == {"Thinking..."}
                if worker_wakeup:
                    worker_done.set()
                    await observer
                    assert set(statuses) == {"Thinking..."}
                worker_status = "success"
                await observer
                await slack_thinking.sync_slack_background_status(client, "worker")
                assert statuses[-1] == "Waiting for background tasks…"
                worker_metadata.clear()
                await slack_thinking.sync_slack_background_status(client, "worker")
            finally:
                observer.cancel()
                await asyncio.gather(observer, return_exceptions=True)
    assert statuses[-1] == ""
    client.runs.join.assert_awaited_once_with(
        "worker" if worker_wakeup else "coordinator",
        "worker-run" if worker_wakeup else "coordinator-run",
    )


async def test_status_wait_cancellation_propagates_and_clears_idle_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    waiting = asyncio.Event()

    async def join(_thread_id: str, _run_id: str) -> dict[str, object]:
        waiting.set()
        await asyncio.Event().wait()
        return {}

    client = _status_client()
    client.runs.join.side_effect = join
    set_status = AsyncMock(return_value=True)
    monkeypatch.setattr(slack_thinking, "set_slack_thread_status", set_status)

    async with asyncio.timeout(2):
        async with asyncio.TaskGroup() as tasks:
            observer = tasks.create_task(
                slack_thinking.show_slack_thinking_status(
                    client=client,
                    thread_id="thread-1",
                    run_id="run-1",
                    channel_id="C1",
                    thread_ts="1.0",
                )
            )
            await waiting.wait()
            observer.cancel()
            with pytest.raises(asyncio.CancelledError):
                await observer

    assert client.runs.join.await_count == 1
    assert set_status.await_args_list == [
        call("C1", "1.0", "Thinking..."),
        call("C1", "1.0", ""),
    ]


async def test_failed_status_clear_stops_retrying(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    set_status = AsyncMock(return_value=False)
    monkeypatch.setattr(slack_thinking, "set_slack_thread_status", set_status)

    await slack_thinking.clear_slack_thinking_status_if_idle(
        _status_client(), "thread-1", "D1", "1.0"
    )

    assert set_status.await_args_list == [call("D1", "1.0", "")] * 3
    assert "cleanup retries exhausted" in caplog.text


async def test_status_clear_retry_preserves_newer_run(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _status_client()
    calls: list[str] = []

    async def set_status(_channel_id: str, thread_ts: str, _status: str) -> bool:
        calls.append(thread_ts)
        client.runs.list.return_value = [{"run_id": "new-run"}]
        return False

    monkeypatch.setattr(slack_thinking, "set_slack_thread_status", set_status)
    await slack_thinking.clear_slack_thinking_status_if_idle(client, "thread-1", "D1", "1.0")

    assert calls == ["1.0"]
