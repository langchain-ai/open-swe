from unittest.mock import AsyncMock

from agent.surfaces import projector


class _Part:
    def __init__(self, event: str, data: dict) -> None:
        self.event = event
        self.data = data


async def test_streams_sanitized_tool_steps(monkeypatch) -> None:
    parts = [
        _Part(
            "tools",
            {
                "event": "tool-started",
                "tool_call_id": "call-1",
                "tool_name": "execute",
                "input": {"command": "echo secret-token"},
            },
        ),
        _Part("tools", {"event": "tool-finished", "tool_call_id": "call-1"}),
    ]

    async def join_stream(*_args, **_kwargs):
        for part in parts:
            yield part

    client = AsyncMock()
    client.runs.join_stream = join_stream
    client.runs.get.return_value = {"status": "success"}
    start = AsyncMock(return_value="2.0")
    append = AsyncMock()
    stop = AsyncMock()
    monkeypatch.setattr(projector, "start_slack_stream", start)
    monkeypatch.setattr(projector, "append_slack_stream", append)
    monkeypatch.setattr(projector, "stop_slack_stream", stop)
    monkeypatch.setattr(projector, "store_slack_run_mapping", AsyncMock())

    await projector.project_run_into_slack(
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
    assert final_chunks[-1]["status"] == "complete"
    assert final_chunks[-1]["output"] == "Completed"


async def test_stop_sends_pending_updates_despite_append_backoff(monkeypatch) -> None:
    stop = AsyncMock()
    monkeypatch.setattr(projector, "stop_slack_stream", stop)
    stream = projector.SlackTranscript(
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
    step = projector.Step("step-1", "Reading", "in_progress")
    stream.steps[((), "call-1")] = step
    stream._queue_step(step)

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
    monkeypatch.setattr(projector, "monotonic", lambda: clock)
    append = AsyncMock(
        side_effect=[projector.SlackStreamError("rate_limited", retry_after=30), None]
    )
    monkeypatch.setattr(projector, "append_slack_stream", append)
    stream = projector.SlackTranscript(
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
    step = projector.Step("step-1", "Reading", "in_progress")
    stream._queue_step(step)

    await stream.flush(force=True)
    clock = 39.0
    await stream.flush(force=True)
    assert append.await_count == 1

    clock = 40.0
    await stream.flush(force=True)
    assert append.await_count == 2
    assert not stream.pending


def test_namespaced_tool_events_have_stable_distinct_ids() -> None:
    stream = projector.SlackTranscript(
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

    stream.consume(_Part("tools|subagent:a", event))
    stream.consume(_Part("tools|subagent:b", event))

    assert len(stream.steps) == 2
    assert {step.title for step in stream.steps.values()} == {"Reading auth.py"}
    assert len({step.task_id for step in stream.steps.values()}) == 2


def _stream(**overrides: object) -> projector.SlackTranscript:
    return projector.SlackTranscript(
        **{
            "client": AsyncMock(),
            "thread_id": "thread-1",
            "run_id": "run-1",
            "channel_id": "C1",
            "thread_ts": "0",
            "recipient_user_id": "U1",
            "recipient_team_id": "T1",
            "mapping_thread_ts": "0",
            "original_message_ts": "1.1",
            **overrides,
        }  # type: ignore[arg-type]
    )


def _say(text: str) -> list[_Part]:
    return [
        _Part("messages", {"event": "message-start", "role": "ai", "id": "m1"}),
        _Part(
            "messages",
            {"event": "content-block-delta", "index": 0, "content": {"type": "text", "text": text}},
        ),
        _Part("messages", {"event": "message-finish", "metadata": {}}),
    ]


async def test_the_agents_words_reach_the_channel_without_a_posting_tool(monkeypatch) -> None:
    append = AsyncMock()
    monkeypatch.setattr(projector, "append_slack_stream", append)
    stream = _stream()
    stream.message_ts = "2.0"

    for part in _say("Looking at the login test now."):
        stream.consume(part)
    await stream.flush(force=True)

    assert append.await_args_list[0].args[2] == [
        {"type": "markdown_text", "text": "Looking at the login test now."}
    ]


def test_text_and_tool_cards_keep_the_order_they_happened() -> None:
    stream = _stream()
    stream.consume(_Part("messages", {"event": "message-start", "role": "ai", "id": "m1"}))
    stream.consume(
        _Part(
            "messages",
            {"event": "content-block-delta", "content": {"type": "text", "text": "First I will "}},
        )
    )
    stream.consume(
        _Part(
            "messages",
            {"event": "content-block-delta", "content": {"type": "text", "text": "read the file."}},
        )
    )
    stream.consume(
        _Part(
            "tools",
            {
                "event": "tool-started",
                "tool_call_id": "call-1",
                "tool_name": "read_file",
                "input": {"file_path": "/workspace/app/auth.py"},
            },
        )
    )
    stream.consume(_Part("messages", {"event": "message-start", "role": "ai", "id": "m2"}))
    stream.consume(
        _Part(
            "messages",
            {"event": "content-block-delta", "content": {"type": "text", "text": "Done."}},
        )
    )

    kinds = [chunk["type"] for chunk in stream.pending]
    assert kinds == ["markdown_text", "task_update", "markdown_text"]
    # Consecutive deltas of one message coalesce into a single chunk.
    assert stream.pending[0]["text"] == "First I will read the file."
    assert stream.pending[2]["text"] == "Done."


def test_a_step_that_updates_replaces_its_chunk_in_place() -> None:
    stream = _stream()
    started = {
        "event": "tool-started",
        "tool_call_id": "call-1",
        "tool_name": "read_file",
        "input": {"file_path": "/workspace/app/auth.py"},
    }
    stream.consume(_Part("tools", started))
    stream.consume(_Part("tools", {"event": "tool-finished", "tool_call_id": "call-1"}))

    assert len(stream.pending) == 1
    assert stream.pending[0]["status"] == "complete"


def test_only_the_top_level_agent_narrates_the_channel() -> None:
    """A subagent has nowhere to be nested in Slack, so only its cards show."""
    stream = _stream()
    stream.consume(
        _Part("messages|subagent:a", {"event": "message-start", "role": "ai", "id": "m1"})
    )
    stream.consume(
        _Part(
            "messages|subagent:a",
            {"event": "content-block-delta", "content": {"type": "text", "text": "inner chatter"}},
        )
    )

    assert stream.pending == []


def test_tool_results_and_reasoning_stay_out_of_the_transcript() -> None:
    stream = _stream()
    stream.consume(_Part("messages", {"event": "message-start", "role": "tool", "id": "m1"}))
    stream.consume(
        _Part(
            "messages",
            {"event": "content-block-delta", "content": {"type": "text", "text": "tool output"}},
        )
    )
    stream.consume(_Part("messages", {"event": "message-finish", "metadata": {}}))
    stream.consume(_Part("messages", {"event": "message-start", "role": "ai", "id": "m2"}))
    stream.consume(
        _Part(
            "messages",
            {
                "event": "content-block-delta",
                "content": {"type": "reasoning", "text": "private thinking"},
            },
        )
    )

    assert stream.pending == []


async def test_a_long_transcript_continues_in_a_second_message(monkeypatch) -> None:
    append = AsyncMock()
    start = AsyncMock(return_value="3.0")
    stop = AsyncMock()
    monkeypatch.setattr(projector, "append_slack_stream", append)
    monkeypatch.setattr(projector, "start_slack_stream", start)
    monkeypatch.setattr(projector, "stop_slack_stream", stop)
    monkeypatch.setattr(projector, "store_slack_run_mapping", AsyncMock())
    monkeypatch.setattr(projector, "store_slack_message_run_mapping", AsyncMock())
    stream = _stream()
    stream.message_ts = "2.0"

    stream.consume(_Part("messages", {"event": "message-start", "role": "ai", "id": "m1"}))
    for _ in range(3):
        stream.consume(
            _Part(
                "messages",
                {
                    "event": "content-block-delta",
                    "content": {"type": "text", "text": "x" * (projector._STREAM_TEXT_LIMIT // 2)},
                },
            )
        )
        await stream.flush(force=True)

    # The first append filled the message; the second rolled over to a new one.
    assert stop.await_args_list[0].kwargs["session_status"] == "processing"
    assert start.await_count == 1
    assert stream.message_ts == "3.0"
    assert [call.args[1] for call in append.await_args_list] == ["2.0", "2.0", "3.0"]


async def test_the_streamed_message_is_mapped_to_its_run(monkeypatch) -> None:
    """Reactions land on the transcript, so that message has to resolve to the run."""
    monkeypatch.setattr(projector, "start_slack_stream", AsyncMock(return_value="2.0"))
    run_mapping = AsyncMock()
    message_mapping = AsyncMock()
    monkeypatch.setattr(projector, "store_slack_run_mapping", run_mapping)
    monkeypatch.setattr(projector, "store_slack_message_run_mapping", message_mapping)

    assert await _stream().start() is True

    assert message_mapping.await_args_list[0].args[3] == "2.0"
    assert message_mapping.await_args_list[0].kwargs["run_id"] == "run-1"
    assert run_mapping.await_args_list[-1].kwargs["thinking_message_ts"] == "2.0"


async def test_closing_out_stops_a_transcript_its_projection_left_open(monkeypatch) -> None:
    monkeypatch.setattr(
        projector,
        "lookup_slack_run_message_mapping",
        AsyncMock(return_value={"thinking_message_ts": "2.0"}),
    )
    stop = AsyncMock()
    monkeypatch.setattr(projector, "stop_slack_stream", stop)

    await projector.close_projection(AsyncMock(), channel_id="C1", run_id="run-1")

    stop.assert_awaited_once_with("C1", "2.0")


async def test_closing_out_a_run_with_no_transcript_does_nothing(monkeypatch) -> None:
    monkeypatch.setattr(projector, "lookup_slack_run_message_mapping", AsyncMock(return_value=None))
    stop = AsyncMock()
    monkeypatch.setattr(projector, "stop_slack_stream", stop)

    await projector.close_projection(AsyncMock(), channel_id="C1", run_id="run-1")

    stop.assert_not_awaited()
