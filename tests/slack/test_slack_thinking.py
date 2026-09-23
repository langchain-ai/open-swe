from unittest.mock import AsyncMock, call

import pytest

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


class _AnchorStore:
    """The session status anchor, as the LangGraph store keeps it."""

    def __init__(self) -> None:
        self.items: dict[tuple[tuple[str, ...], str], dict[str, object]] = {}

    async def get_item(self, namespace: tuple[str, ...], key: str) -> dict[str, object] | None:
        value = self.items.get((namespace, key))
        return {"value": value} if value is not None else None

    async def put_item(
        self, namespace: tuple[str, ...], key: str, value: dict[str, object]
    ) -> None:
        self.items[(namespace, key)] = value

    async def delete_item(self, namespace: tuple[str, ...], key: str) -> None:
        self.items.pop((namespace, key), None)


def _status_client(store: _AnchorStore) -> AsyncMock:
    client = AsyncMock()
    client.store = store
    client.runs.list.return_value = []
    client.threads.get.return_value = {"metadata": {}}
    return client


async def test_session_status_moves_to_the_newest_message(monkeypatch) -> None:
    """A DM shows one indicator, on the message being answered, then none."""
    calls: list[tuple[str, str, str]] = []

    async def set_status(channel_id: str, thread_ts: str, status: str) -> bool:
        calls.append((channel_id, thread_ts, status))
        return True

    monkeypatch.setattr(slack_thinking, "set_slack_thread_status", set_status)
    monkeypatch.setattr(slack_thinking, "_STATUS_REFRESH_SECONDS", 3600.0)
    store = _AnchorStore()

    await slack_thinking.show_slack_thinking_status(
        client=_status_client(store),
        thread_id="t1",
        run_id="run-1",
        channel_id="D1",
        thread_ts="111.0",
        session_ts="0",
    )
    await slack_thinking.show_slack_thinking_status(
        client=_status_client(store),
        thread_id="t1",
        run_id="run-2",
        channel_id="D1",
        thread_ts="222.0",
        session_ts="0",
    )

    assert calls == [
        ("D1", "111.0", "Thinking..."),
        ("D1", "111.0", ""),
        ("D1", "222.0", "Thinking..."),
        ("D1", "222.0", ""),
    ]
    assert store.items == {}


async def test_session_status_release_leaves_a_newer_runs_indicator_alone(monkeypatch) -> None:
    """The run that finishes first must not clear the indicator someone is waiting on."""
    cleared: list[str] = []

    async def set_status(channel_id: str, thread_ts: str, status: str) -> bool:  # noqa: ARG001
        if not status:
            cleared.append(thread_ts)
        return True

    monkeypatch.setattr(slack_thinking, "set_slack_thread_status", set_status)
    store = _AnchorStore()
    client = _status_client(store)

    # The newer run already owns the session's status.
    await slack_thinking._claim_status_anchor(client, "D1", "0", "222.0")

    released = await slack_thinking._release_status_anchor(client, "D1", "0", "111.0")

    assert released is False
    assert cleared == []
    assert store.items[(("slack_session_status_anchor", "D1"), "0")] == {"message_ts": "222.0"}


async def test_finished_run_clears_without_completion_events(monkeypatch) -> None:
    """An already-finished run clears without a lifecycle event or completion webhook."""
    set_status = AsyncMock(return_value=True)
    monkeypatch.setattr(slack_thinking, "set_slack_thread_status", set_status)
    monkeypatch.setattr(slack_thinking, "_STATUS_REFRESH_SECONDS", 0.0)

    client = _status_client(_AnchorStore())
    client.runs.list = AsyncMock(return_value=[])

    await slack_thinking.show_slack_thinking_status(
        client=client,
        thread_id="thread-1",
        run_id="run-1",
        channel_id="C1",
        thread_ts="1.0",
    )

    assert set_status.await_args_list == [
        call("C1", "1.0", slack_thinking._THINKING_STATUS),
        call("C1", "1.0", ""),
    ]


@pytest.mark.parametrize("session", [False, True])
async def test_status_poll_retries_failed_cleanup(
    monkeypatch: pytest.MonkeyPatch, session: bool
) -> None:
    store = _AnchorStore()
    client = _status_client(store)
    set_status = AsyncMock(side_effect=[True, False, True])
    monkeypatch.setattr(slack_thinking, "set_slack_thread_status", set_status)

    async def retry(_seconds: float) -> None:
        assert set_status.await_args == call("C1", "1.0", "")
        if session:
            assert store.items[(("slack_session_status_anchor", "C1"), "0")] == {
                "message_ts": "1.0"
            }

    monkeypatch.setattr(slack_thinking.asyncio, "sleep", retry)
    await slack_thinking.show_slack_thinking_status(
        client=client,
        thread_id="thread-1",
        run_id="run-1",
        channel_id="C1",
        thread_ts="1.0",
        session_ts="0" if session else "",
    )

    assert set_status.await_args_list == [
        call("C1", "1.0", "Thinking..."),
        call("C1", "1.0", ""),
        call("C1", "1.0", ""),
    ]
    assert store.items == {}


async def test_status_refresh_checks_run_state_and_recovers_lookup_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _status_client(_AnchorStore())
    client.runs.list.side_effect = [RuntimeError("unavailable"), [{"id": "run-1"}]]
    set_status = AsyncMock(return_value=True)
    monkeypatch.setattr(slack_thinking, "set_slack_thread_status", set_status)
    monkeypatch.setattr(slack_thinking, "_STATUS_REFRESH_SECONDS", 0.0)
    sleeps = 0

    async def finish(_seconds: float) -> None:
        nonlocal sleeps
        sleeps += 1
        assert call("C1", "1.0", "") not in set_status.await_args_list
        if sleeps == 2:
            client.runs.list.side_effect = None
            client.runs.list.return_value = []

    monkeypatch.setattr(slack_thinking.asyncio, "sleep", finish)
    await slack_thinking.show_slack_thinking_status(
        client=client,
        thread_id="thread-1",
        run_id="run-1",
        channel_id="C1",
        thread_ts="1.0",
    )
    assert set_status.await_args_list == [
        call("C1", "1.0", "Thinking..."),
        call("C1", "1.0", "Thinking..."),
        call("C1", "1.0", "Thinking..."),
        call("C1", "1.0", ""),
    ]


async def test_early_status_survives_while_another_run_is_active(monkeypatch) -> None:
    set_status = AsyncMock(return_value=True)
    monkeypatch.setattr(slack_thinking, "set_slack_thread_status", set_status)
    client = _status_client(_AnchorStore())
    client.runs.list = AsyncMock(return_value=[{"id": "run-2"}])

    await slack_thinking.clear_slack_thinking_status_if_idle(client, "thread-1", "C1", "1.0")

    set_status.assert_not_awaited()


async def test_thread_status_survives_while_another_run_is_active(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    set_status = AsyncMock(return_value=True)
    monkeypatch.setattr(slack_thinking, "set_slack_thread_status", set_status)
    client = _status_client(_AnchorStore())
    client.runs.list.return_value = [{"id": "run-2"}]

    async def finish_run(_seconds: float) -> None:
        assert call("C1", "1.0", "") not in set_status.await_args_list
        client.runs.list.return_value = []

    monkeypatch.setattr(slack_thinking.asyncio, "sleep", finish_run)
    await slack_thinking.show_slack_thinking_status(
        client=client,
        thread_id="thread-1",
        run_id="run-1",
        channel_id="C1",
        thread_ts="1.0",
    )

    assert set_status.await_args == call("C1", "1.0", "")


@pytest.mark.parametrize("session", [False, True])
async def test_background_waiting_resumes_and_clears(monkeypatch, session: bool) -> None:
    set_status = AsyncMock(return_value=True)
    monkeypatch.setattr(slack_thinking, "set_slack_thread_status", set_status)
    store = _AnchorStore()
    client = _status_client(store)
    metadata = {
        "running_background_tasks": ["cmd-1", "cmd-2"],
        "source_context": {
            "slack_thread": {"channel_id": "C1", "thread_ts": "0" if session else "1.0"}
        },
    }
    client.threads.get.return_value = {"metadata": metadata}

    await slack_thinking.show_slack_thinking_status(
        client=client,
        thread_id="thread-1",
        run_id="run-1",
        channel_id="C1",
        thread_ts="1.0",
        session_ts="0" if session else "",
    )
    assert set_status.await_args == call("C1", "1.0", "Waiting for background tasks…")

    client.runs.list.return_value = [{"run_id": "completion-run"}]
    await slack_thinking.sync_slack_background_status(client, "thread-1", resume=True)
    assert set_status.await_args == call("C1", "1.0", "Thinking...")
    set_status.reset_mock()
    await slack_thinking.sync_slack_background_status(client, "thread-1")
    set_status.assert_not_awaited()

    client.runs.list.return_value = []
    metadata["running_background_tasks"] = ["cmd-2"]
    await slack_thinking.sync_slack_background_status(client, "thread-1")
    set_status.assert_awaited_once_with("C1", "1.0", "Waiting for background tasks…")

    metadata["running_background_tasks"] = []
    await slack_thinking.sync_slack_background_status(client, "thread-1")
    assert set_status.await_args == call("C1", "1.0", "")
    assert store.items == {}


async def test_idle_settlement_preserves_newer_session_anchor(monkeypatch) -> None:
    set_status = AsyncMock(return_value=True)
    monkeypatch.setattr(slack_thinking, "set_slack_thread_status", set_status)
    client = _status_client(_AnchorStore())
    client.threads.get.return_value = {"metadata": {"running_background_tasks": ["cmd-1"]}}
    await slack_thinking._claim_status_anchor(client, "D1", "0", "222.0")
    await slack_thinking.clear_slack_thinking_status_if_idle(
        client, "thread-1", "D1", "111.0", session_ts="0"
    )
    set_status.assert_not_awaited()


async def test_run_lookup_failure_does_not_replace_working_status(monkeypatch) -> None:
    set_status = AsyncMock(return_value=True)
    monkeypatch.setattr(slack_thinking, "set_slack_thread_status", set_status)
    client = _status_client(_AnchorStore())
    client.runs.list.side_effect = RuntimeError("unavailable")
    client.threads.get.return_value = {"metadata": {"running_background_tasks": ["cmd-1"]}}
    await slack_thinking.clear_slack_thinking_status_if_idle(client, "thread-1", "C1", "1.0")
    set_status.assert_not_awaited()


@pytest.mark.parametrize("provided_metadata", [False, True])
@pytest.mark.parametrize("waiting", [False, True])
async def test_status_sync_reuses_metadata_for_idle_settlement(
    monkeypatch: pytest.MonkeyPatch, provided_metadata: bool, waiting: bool
) -> None:
    client = _status_client(_AnchorStore())
    metadata: dict[str, object] = {
        "running_background_tasks": ["cmd-1"] if waiting else [],
        "source_context": {"slack_thread": {"channel_id": "C1", "thread_ts": "1.0"}},
    }
    client.threads.get.return_value = {"metadata": metadata}
    set_status = AsyncMock()
    monkeypatch.setattr(slack_thinking, "set_slack_thread_status", set_status)
    await slack_thinking.sync_slack_background_status(
        client, "thread-1", metadata=metadata if provided_metadata else None
    )
    assert client.threads.get.await_count == (0 if provided_metadata else 1)
    set_status.assert_awaited_once_with(
        "C1", "1.0", "Waiting for background tasks…" if waiting else ""
    )


async def test_status_sync_does_not_clear_run_started_during_settlement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _status_client(_AnchorStore())
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


@pytest.mark.parametrize("waiting", [False, True])
async def test_status_sync_preserves_anchor_replaced_during_settlement(
    monkeypatch: pytest.MonkeyPatch, waiting: bool
) -> None:
    client = AsyncMock()
    client.runs.list.return_value = []
    client.store.get_item.side_effect = [
        {"value": {"message_ts": "1.0"}},
        {"value": {"message_ts": "2.0"}},
    ]
    set_status = AsyncMock()
    monkeypatch.setattr(slack_thinking, "set_slack_thread_status", set_status)
    await slack_thinking.sync_slack_background_status(
        client,
        "thread-1",
        metadata={
            "running_background_tasks": ["cmd-1"] if waiting else [],
            "source_context": {"slack_thread": {"channel_id": "D1", "thread_ts": "0"}},
        },
    )
    client.threads.get.assert_not_awaited()
    client.store.delete_item.assert_not_awaited()
    set_status.assert_not_awaited()
