import asyncio
from unittest.mock import AsyncMock, Mock, call

import httpx
import pytest

from agent.slack import thinking as slack_thinking


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
    client.threads.stream = Mock(side_effect=RuntimeError("stream unavailable"))
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

    async def set_status(
        _channel_id: str,
        _thread_ts: str,
        status: str,
        *,
        loading_messages: list[str] | None = None,
    ) -> bool:
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
        call("C1", "1.0", "Thinking...", loading_messages=slack_thinking._LOADING_MESSAGES),
        call("C1", "1.0", ""),
    ]


async def test_tool_status_is_run_scoped_and_cleared_after_completion(
    monkeypatch: pytest.MonkeyPatch, slack_api
) -> None:
    client = _status_client()
    observed = asyncio.Event()
    complete = asyncio.Event()

    def lifecycle(run_id: str, phase: str) -> dict[str, object]:
        event = _event("lifecycle", {"event": phase})
        event["event_id"] = f"synth:{run_id}:lc||{phase}"
        return event

    tool = _event(
        "tools",
        {
            "event": "tool-started",
            "tool_name": "execute",
            "input": {"command": "echo secret-token"},
        },
    )

    class ThreadStream:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            pass

        def subscribe(self, _channels):
            async def iterator():
                yield tool
                yield lifecycle("other-run", "running")
                yield tool
                yield lifecycle("run-1", "running")
                yield {**tool, "params": {**tool["params"], "namespace": ["subagent"]}}
                yield tool
                yield tool
                observed.set()
                await complete.wait()
                yield lifecycle("run-1", "completed")
                yield _event("tools", {"event": "tool-started", "tool_name": "write_file"})

            return iterator()

    async def join(_thread_id: str, _run_id: str) -> dict[str, object]:
        await complete.wait()
        return {}

    client.threads.stream = lambda *_args, **_kwargs: ThreadStream()
    client.runs.join.side_effect = join

    async with asyncio.timeout(2):
        observer = asyncio.create_task(
            slack_thinking.show_slack_thinking_status(
                client=client,
                thread_id="thread-1",
                run_id="run-1",
                channel_id="C1",
                thread_ts="1.0",
            )
        )
        await observed.wait()
        complete.set()
        await observer

    payloads = [
        payload for method, payload in slack_api.calls if method == "assistant.threads.setStatus"
    ]
    assert [payload["status"] for payload in payloads] == [
        "Thinking...",
        "Running a development command",
        "",
    ]
    assert payloads[0]["loading_messages"]
    assert all("loading_messages" not in payload for payload in payloads[1:])
    assert "secret-token" not in str(payloads)


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
