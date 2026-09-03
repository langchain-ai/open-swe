from unittest.mock import AsyncMock, patch

import pytest

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
    assert stream.pending[0]["title"] == "Read auth.py"
    assert stream.pending[0]["status"] == "complete"
    # The status carries it; a "Completed" line would say it twice.
    assert "output" not in stream.pending[0]


def test_a_failed_tool_shows_as_failed() -> None:
    stream = _stream()

    stream.tool_started("call-1", "execute", {"command": "pytest"})
    stream.tool_finished("call-1", failed=True)

    assert stream.pending[0]["status"] == "error"


def test_a_step_is_titled_by_what_it_did() -> None:
    """The argument that identifies the step is what a reader scans for."""
    stream = _stream()

    stream.tool_started("call-1", "execute", {"command": "pytest tests/auth"})
    stream.tool_started("call-2", "grep", {"pattern": "login_token"})
    stream.tool_started("call-3", "read_file", {"file_path": "/workspace/app/auth.py"})

    assert [chunk["title"] for chunk in stream.pending] == [
        "pytest tests/auth",
        "Searched for login_token",
        "Read auth.py",
    ]
    assert all("details" not in chunk for chunk in stream.pending)


def test_a_long_command_is_cut_to_a_title() -> None:
    stream = _stream()

    stream.tool_started("call-1", "execute", {"command": "echo " + "x" * 300})

    title = stream.pending[0]["title"]
    assert len(title) == 120
    assert title.endswith("…")


async def test_a_turn_opens_its_message_with_nothing_in_it(monkeypatch) -> None:
    """Nothing is said until the agent says it; the session status shows the wait."""
    start = AsyncMock(return_value="2.0")
    monkeypatch.setattr(projector, "start_slack_stream", start)
    monkeypatch.setattr(projector, "store_slack_message_run_mapping", AsyncMock())
    stream = _stream()

    assert await stream.start() is True

    assert start.await_args is not None
    assert start.await_args.args[2] == []
    assert stream.pending == []


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
    assert "output" not in final[-1]
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
    monkeypatch.setattr(projector, "store_slack_message_run_mapping", AsyncMock())
    stream = _stream()
    stream.message_ts = "2.0"

    for _ in range(3):
        stream.say("x" * (projector._STREAM_TEXT_LIMIT // 2))
        await stream.flush(force=True)

    assert stop.await_args_list[0].kwargs["session_status"] == "processing"
    assert stream.message_ts == "3.0"
    assert [call.args[1] for call in append.await_args_list] == ["2.0", "2.0", "3.0"]


async def test_the_streamed_message_is_mapped_without_a_run_id(monkeypatch) -> None:
    """Reactions resolve through the thread's mapping, which holds the real run."""
    monkeypatch.setattr(projector, "start_slack_stream", AsyncMock(return_value="2.0"))
    message_mapping = AsyncMock()
    monkeypatch.setattr(projector, "store_slack_message_run_mapping", message_mapping)

    assert await _stream().start() is True

    assert message_mapping.await_args is not None
    assert message_mapping.await_args.args[3] == "2.0"
    assert "run_id" not in message_mapping.await_args.kwargs


async def test_a_channel_that_will_not_stream_is_not_fatal(monkeypatch) -> None:
    monkeypatch.setattr(
        projector,
        "start_slack_stream",
        AsyncMock(side_effect=projector.SlackStreamError("channel_not_found")),
    )

    assert await _stream().start() is False


async def test_closing_out_stops_a_transcript_left_open() -> None:
    client = AsyncMock()
    client.store.get_item.return_value = {"value": {"message_ts": "2.0", "channel_id": "C1"}}
    stop = AsyncMock()
    with patch.object(projector, "stop_slack_stream", stop):
        await projector.close_transcript(client, thread_id="thread-1", run_key="prepare-1")

    stop.assert_awaited_once_with("C1", "2.0", [])
    assert client.store.get_item.await_args.args[0] == ("slack_transcript", "thread-1")


async def test_closing_out_says_what_slack_had_held_back() -> None:
    """A rate-limited append leaves words in the record; this is the last chance."""
    held = [{"type": "markdown_text", "text": "the last thing it said"}]
    client = AsyncMock()
    client.store.get_item.return_value = {
        "value": {"message_ts": "2.0", "channel_id": "C1", "pending": held}
    }
    stop = AsyncMock()
    with patch.object(projector, "stop_slack_stream", stop):
        await projector.close_transcript(client, thread_id="thread-1", run_key="prepare-1")

    stop.assert_awaited_once_with("C1", "2.0", held)


@pytest.mark.parametrize(
    "record",
    [
        None,
        {},
        {"message_ts": "2.0"},
        {"channel_id": "C1"},
        {"message_ts": "2.0", "channel_id": "C1", "done": True},
    ],
    ids=["no record", "empty", "no channel", "no message", "already closed"],
)
async def test_closing_out_leaves_alone_what_it_cannot_or_need_not_stop(
    record: dict[str, object] | None,
) -> None:
    client = AsyncMock()
    client.store.get_item.return_value = {"value": record} if record is not None else None
    stop = AsyncMock()
    with patch.object(projector, "stop_slack_stream", stop):
        await projector.close_transcript(client, thread_id="thread-1", run_key="prepare-1")

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
    monkeypatch.setattr(projector, "store_slack_message_run_mapping", AsyncMock())
    stream = _stream()
    stream.message_ts = "2.0"

    stream.say("x" * (projector._STREAM_TEXT_LIMIT * 2 + 100))
    await stream.flush(force=True)

    assert [message_ts for message_ts, _ in appended] == ["2.0", "3.0", "4.0"]
    assert all(chars <= projector._STREAM_TEXT_LIMIT for _, chars in appended)
    assert sum(chars for _, chars in appended) == projector._STREAM_TEXT_LIMIT * 2 + 100
    assert not stream.pending


def test_a_restored_queue_keeps_its_cards_identifiable() -> None:
    """A tool that started before a resume must complete its own card, not a new one."""
    first = _stream()
    first.tool_started("call-1", "execute", {"command": "pytest tests/auth"})
    carried = list(first.pending)

    resumed = _stream()
    resumed.restore_pending(carried)
    resumed.tool_finished("call-1")

    assert len(resumed.pending) == 1
    assert resumed.pending[0]["title"] == "pytest tests/auth"
    assert resumed.pending[0]["status"] == "complete"


def test_a_restored_queue_keeps_text_where_it_was() -> None:
    first = _stream()
    first.say("Running the tests.")
    first.tool_started("call-1", "execute", {"command": "pytest"})
    carried = list(first.pending)

    resumed = _stream()
    resumed.restore_pending(carried)
    resumed.tool_finished("call-1")

    assert [chunk["type"] for chunk in resumed.pending] == ["markdown_text", "task_update"]
    assert resumed.pending[0]["text"] == "Running the tests."
