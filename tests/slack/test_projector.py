from unittest.mock import AsyncMock

from agent.surfaces import projector


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
        }  # type: ignore[arg-type]
    )


async def test_the_agents_words_reach_the_channel(monkeypatch) -> None:
    append = AsyncMock()
    monkeypatch.setattr(projector, "append_slack_stream", append)
    stream = _stream()
    stream.message_ts = "2.0"

    stream.say("Looking at the login test now.")
    await stream.flush(force=True)

    assert append.await_args_list[0].args[2] == [
        {"type": "markdown_text", "text": "Looking at the login test now."}
    ]


def test_words_and_tool_cards_keep_the_order_they_happened() -> None:
    stream = _stream()

    stream.say("First I will ")
    stream.say("read the file.")
    stream.tool_started("call-1", "read_file", {"file_path": "/workspace/app/auth.py"})
    stream.say("Done.")

    assert [chunk["type"] for chunk in stream.pending] == [
        "markdown_text",
        "task_update",
        "markdown_text",
    ]
    # Consecutive words coalesce into one chunk.
    assert stream.pending[0]["text"] == "First I will read the file."
    assert stream.pending[2]["text"] == "Done."


def test_a_tool_card_updates_in_place() -> None:
    stream = _stream()

    stream.tool_started("call-1", "read_file", {"file_path": "/workspace/app/auth.py"})
    stream.tool_finished("call-1")

    assert len(stream.pending) == 1
    assert stream.pending[0]["title"] == "Reading auth.py"
    assert stream.pending[0]["status"] == "complete"


def test_a_failed_tool_shows_as_failed() -> None:
    stream = _stream()

    stream.tool_started("call-1", "execute", {"command": "pytest"})
    stream.tool_finished("call-1", failed=True)

    assert stream.pending[0]["status"] == "error"
    assert stream.pending[0]["output"] == "Failed"


def test_a_tool_input_is_summarized_not_echoed() -> None:
    stream = _stream()

    stream.tool_started("call-1", "execute", {"command": "echo secret-token"})

    assert stream.pending[0]["title"] == "Running a development command"


async def test_the_first_tool_call_completes_the_startup_card(monkeypatch) -> None:
    monkeypatch.setattr(projector, "start_slack_stream", AsyncMock(return_value="2.0"))
    monkeypatch.setattr(projector, "store_slack_run_mapping", AsyncMock())
    monkeypatch.setattr(projector, "store_slack_message_run_mapping", AsyncMock())
    stream = _stream()
    assert await stream.start() is True

    stream.tool_started("call-1", "read_file", {"file_path": "/workspace/a.py"})

    assert stream.pending[0]["status"] == "complete"
    assert stream.pending[0]["title"] == "Preparing the agent workspace"


async def test_stop_finishes_whatever_was_still_running(monkeypatch) -> None:
    stop = AsyncMock()
    monkeypatch.setattr(projector, "stop_slack_stream", stop)
    stream = _stream()
    stream.message_ts = "2.0"
    stream.retry_at = float("inf")
    stream.tool_started("call-1", "read_file", {"file_path": "/workspace/a.py"})

    await stream.stop("success")

    assert stop.await_args is not None
    final = stop.await_args.args[2]
    assert final[-1]["status"] == "complete"
    assert final[-1]["output"] == "Completed"
    assert not stream.pending


async def test_rate_limit_defers_the_append_until_retry_after(monkeypatch) -> None:
    clock = 10.0
    monkeypatch.setattr(projector, "monotonic", lambda: clock)
    append = AsyncMock(
        side_effect=[projector.SlackStreamError("rate_limited", retry_after=30), None]
    )
    monkeypatch.setattr(projector, "append_slack_stream", append)
    stream = _stream()
    stream.message_ts = "2.0"
    stream.say("hello")

    await stream.flush(force=True)
    clock = 39.0
    await stream.flush(force=True)
    assert append.await_count == 1

    clock = 40.0
    await stream.flush(force=True)
    assert append.await_count == 2
    assert not stream.pending


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

    for _ in range(3):
        stream.say("x" * (projector._STREAM_TEXT_LIMIT // 2))
        await stream.flush(force=True)

    assert stop.await_args_list[0].kwargs["session_status"] == "processing"
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


async def test_a_channel_that_will_not_stream_is_not_fatal(monkeypatch) -> None:
    monkeypatch.setattr(
        projector,
        "start_slack_stream",
        AsyncMock(side_effect=projector.SlackStreamError("channel_not_found")),
    )

    assert await _stream().start() is False


async def test_closing_out_stops_a_transcript_left_open(monkeypatch) -> None:
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


def test_namespaced_tool_ids_stay_distinct() -> None:
    stream = _stream()

    first = projector._step_id("run-1", ("subagent:a",), "same-call")
    second = projector._step_id("run-1", ("subagent:b",), "same-call")

    assert first != second
    assert isinstance(stream, projector.SlackTranscript)


async def test_no_message_is_left_over_slacks_text_cap(monkeypatch) -> None:
    """One flush can outgrow one Slack message, so it has to span several."""
    appended: list[tuple[str, int]] = []

    async def append(_channel: str, message_ts: str, chunks: list[dict[str, object]]) -> None:
        appended.append(
            (
                message_ts,
                sum(
                    len(str(chunk.get("text", "")))
                    for chunk in chunks
                    if chunk.get("type") == "markdown_text"
                ),
            )
        )

    starts = iter(["3.0", "4.0"])
    monkeypatch.setattr(projector, "append_slack_stream", append)
    monkeypatch.setattr(
        projector, "start_slack_stream", AsyncMock(side_effect=lambda *a, **k: next(starts))
    )
    monkeypatch.setattr(projector, "stop_slack_stream", AsyncMock())
    monkeypatch.setattr(projector, "store_slack_run_mapping", AsyncMock())
    monkeypatch.setattr(projector, "store_slack_message_run_mapping", AsyncMock())
    stream = _stream()
    stream.message_ts = "2.0"

    stream.say("x" * (projector._STREAM_TEXT_LIMIT * 2 + 100))
    await stream.flush(force=True)

    assert [message_ts for message_ts, _ in appended] == ["2.0", "3.0", "4.0"]
    assert all(chars <= projector._STREAM_TEXT_LIMIT for _, chars in appended)
    assert sum(chars for _, chars in appended) == projector._STREAM_TEXT_LIMIT * 2 + 100
    assert not stream.pending
