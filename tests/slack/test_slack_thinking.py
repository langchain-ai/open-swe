from unittest.mock import AsyncMock

from agent.slack import thinking as slack_thinking


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


def test_namespaced_tool_events_have_stable_distinct_ids() -> None:
    stream = slack_thinking.SlackThinkingStream(
        client=AsyncMock(),
        thread_id="thread-1",
        run_id="run-1",
        channel_id="C1",
        thread_ts="1.0",
        recipient_user_id="U1",
        recipient_team_id="T1",
        mapping_thread_ts="1.0",
        original_message_ts="1.1",
    )
    event = {
        "event": "tool-started",
        "tool_call_id": "same-call",
        "tool_name": "read_file",
        "input": {"file_path": "/workspace/app/auth.py"},
    }

    stream.consume(_event("tools", event, namespace=["subagent:a"]))
    stream.consume(_event("tools", event, namespace=["subagent:b"]))

    assert len(stream.steps) == 2
    assert {step.title for step in stream.steps.values()} == {"Reading auth.py"}
    assert len({step.task_id for step in stream.steps.values()}) == 2
