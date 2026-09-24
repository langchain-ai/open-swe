import asyncio
from unittest.mock import AsyncMock, call

import httpx
import pytest
from langgraph_sdk.client import LangGraphClient

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
    client.runs.join.return_value = {}
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


async def test_thread_status_refresh_finishes_before_completion_clears(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    refreshing = asyncio.Event()
    cancelling = asyncio.Event()
    finish_refresh = asyncio.Event()
    calls: list[str] = []

    async def set_status(_channel_id: str, _thread_ts: str, status: str) -> bool:
        calls.append(status)
        if len(calls) == 2:
            refreshing.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelling.set()
                await finish_refresh.wait()
                calls.append("refresh finished")
        return True

    async def join(_thread_id: str, _run_id: str) -> dict[str, object]:
        await refreshing.wait()
        return {}

    monkeypatch.setattr(slack_thinking, "set_slack_thread_status", set_status)
    monkeypatch.setattr(slack_thinking, "_STATUS_REFRESH_SECONDS", 0.0)
    client = _status_client(_AnchorStore())
    client.runs.join.side_effect = join

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
            await cancelling.wait()
            assert not observer.done()
            assert calls == ["Thinking...", "Thinking..."]
            finish_refresh.set()

    assert calls == ["Thinking...", "Thinking...", "refresh finished", ""]


async def test_thread_status_follows_a_mid_run_breakout(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str, str]] = []
    refreshed_destination = asyncio.Event()
    client = _status_client(_AnchorStore())

    async def set_status(channel_id: str, thread_ts: str, status: str) -> bool:
        calls.append((channel_id, thread_ts, status))
        if len(calls) == 1:
            client.threads.get.return_value = {
                "metadata": {
                    "source_context": {"slack_thread": {"channel_id": "C2", "thread_ts": "2.0"}}
                }
            }
        elif channel_id == "C2" and status:
            refreshed_destination.set()
        return True

    async def join(_thread_id: str, _run_id: str) -> dict[str, object]:
        await refreshed_destination.wait()
        return {}

    monkeypatch.setattr(slack_thinking, "set_slack_thread_status", set_status)
    monkeypatch.setattr(slack_thinking, "_STATUS_REFRESH_SECONDS", 0.0)
    client.runs.join.side_effect = join

    async with asyncio.timeout(2):
        await slack_thinking.show_slack_thinking_status(
            client=client,
            thread_id="thread-1",
            run_id="run-1",
            channel_id="C1",
            thread_ts="1.0",
        )

    assert calls[0] == ("C1", "1.0", "Thinking...")
    assert all(channel_id == "C2" for channel_id, _, _ in calls[1:])
    assert calls[-1] == ("C2", "2.0", "")


async def test_early_status_survives_while_another_run_is_active(monkeypatch) -> None:
    set_status = AsyncMock(return_value=True)
    monkeypatch.setattr(slack_thinking, "set_slack_thread_status", set_status)
    client = _status_client(_AnchorStore())
    client.runs.list = AsyncMock(return_value=[{"id": "run-2"}])

    await slack_thinking.clear_slack_thinking_status_if_idle(client, "thread-1", "C1", "1.0")

    set_status.assert_not_awaited()


async def test_thread_status_survives_while_another_run_is_active(monkeypatch) -> None:
    """A completion landing mid-run leaves the active run's indicator alone."""
    set_status = AsyncMock(return_value=True)
    monkeypatch.setattr(slack_thinking, "set_slack_thread_status", set_status)
    monkeypatch.setattr(slack_thinking, "_STATUS_REFRESH_SECONDS", 3600.0)

    client = _status_client(_AnchorStore())
    client.runs.list = AsyncMock(return_value=[{"id": "run-2"}])

    await slack_thinking.show_slack_thinking_status(
        client=client,
        thread_id="thread-1",
        run_id="run-1",
        channel_id="C1",
        thread_ts="1.0",
    )

    assert call("C1", "1.0", "") not in set_status.await_args_list


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
    assert set_status.await_args.args == ("C1", "1.0", "Waiting for background tasks…")

    client.runs.list.return_value = [{"run_id": "completion-run"}]
    await slack_thinking.sync_slack_background_status(client, "thread-1", resume=True)
    assert set_status.await_args.args == ("C1", "1.0", "Thinking...")
    set_status.reset_mock()
    await slack_thinking.sync_slack_background_status(client, "thread-1")
    set_status.assert_not_awaited()

    client.runs.list.return_value = []
    metadata["running_background_tasks"] = ["cmd-2"]
    await slack_thinking.sync_slack_background_status(client, "thread-1")
    set_status.assert_awaited_once_with("C1", "1.0", "Waiting for background tasks…")

    metadata["running_background_tasks"] = []
    await slack_thinking.sync_slack_background_status(client, "thread-1")
    assert set_status.await_args.args == ("C1", "1.0", "")
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


async def test_status_wait_recovers_connection_failure_without_webhook(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _status_client(_AnchorStore())
    client.runs.join.side_effect = [httpx.ConnectError("disconnected"), {}]
    set_status = AsyncMock(return_value=True)
    monkeypatch.setattr(slack_thinking, "set_slack_thread_status", set_status)

    await slack_thinking.show_slack_thinking_status(
        client=client,
        thread_id="thread-1",
        run_id="run-1",
        channel_id="C1",
        thread_ts="1.0",
    )

    assert client.runs.join.await_count == 2
    assert set_status.await_args_list == [
        call("C1", "1.0", "Thinking..."),
        call("C1", "1.0", ""),
    ]


@pytest.mark.parametrize("session", [False, True])
async def test_status_wait_keeps_refreshing_after_repeated_failures(
    monkeypatch: pytest.MonkeyPatch,
    session: bool,
) -> None:
    refreshed = asyncio.Event()
    complete = asyncio.Event()
    statuses: list[str] = []
    store = _AnchorStore()
    client = _status_client(store)
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
                    session_ts="0" if session else "",
                )
            )
            await refreshed.wait()
            assert not observer.done()
            assert set(statuses) == {"Thinking..."}
            complete.set()

    assert statuses[-1] == ""
    assert store.items == {}


async def test_status_wait_cancellation_propagates_and_clears_idle_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    waiting = asyncio.Event()

    async def join(_thread_id: str, _run_id: str) -> dict[str, object]:
        waiting.set()
        await asyncio.Event().wait()
        return {}

    client = _status_client(_AnchorStore())
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


@pytest.mark.parametrize("session", [False, True])
async def test_failed_status_clear_retries_without_losing_anchor(
    monkeypatch: pytest.MonkeyPatch,
    session: bool,
) -> None:
    store = _AnchorStore()
    client = _status_client(store)
    anchor_key = (("slack_session_status_anchor", "C1"), "0")
    if session:
        store.items[anchor_key] = {"message_ts": "1.0"}
    statuses: list[str] = []

    async def set_status(_channel_id: str, _thread_ts: str, status: str) -> bool:
        if session:
            assert store.items[anchor_key] == {"message_ts": "1.0"}
        statuses.append(status)
        return len(statuses) == 2

    monkeypatch.setattr(slack_thinking, "set_slack_thread_status", set_status)
    await slack_thinking.clear_slack_thinking_status_if_idle(
        client,
        "thread-1",
        "C1",
        "1.0",
        session_ts="0" if session else "",
    )

    assert statuses == ["", ""]
    assert store.items == {}


async def test_failed_status_clear_stops_retrying_and_retains_anchor(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    store = _AnchorStore()
    anchor_key = (("slack_session_status_anchor", "D1"), "0")
    store.items[anchor_key] = {"message_ts": "1.0"}
    set_status = AsyncMock(return_value=False)
    monkeypatch.setattr(slack_thinking, "set_slack_thread_status", set_status)

    await slack_thinking.clear_slack_thinking_status_if_idle(
        _status_client(store),
        "thread-1",
        "D1",
        "1.0",
        session_ts="0",
    )

    assert set_status.await_args_list == [call("D1", "1.0", "")] * 3
    assert store.items[anchor_key] == {"message_ts": "1.0"}
    assert "cleanup retries exhausted" in caplog.text


@pytest.mark.parametrize("failed_read", ["runs", "metadata"])
async def test_status_clear_recovers_transient_read_failure(
    monkeypatch: pytest.MonkeyPatch,
    failed_read: str,
) -> None:
    client = _status_client(_AnchorStore())
    if failed_read == "runs":
        client.runs.list.side_effect = [httpx.ConnectError("unavailable"), [], []]
    else:
        client.threads.get.side_effect = [httpx.ConnectError("unavailable"), {"metadata": {}}]
    set_status = AsyncMock(return_value=True)
    monkeypatch.setattr(slack_thinking, "set_slack_thread_status", set_status)

    await slack_thinking.clear_slack_thinking_status_if_idle(client, "thread-1", "C1", "1.0")

    set_status.assert_awaited_once_with("C1", "1.0", "")


@pytest.mark.parametrize("new_owner", ["run", "anchor"])
async def test_status_clear_retry_preserves_newer_work(
    monkeypatch: pytest.MonkeyPatch,
    new_owner: str,
) -> None:
    store = _AnchorStore()
    anchor_key = (("slack_session_status_anchor", "D1"), "0")
    store.items[anchor_key] = {"message_ts": "1.0"}
    client = _status_client(store)
    calls: list[str] = []

    async def set_status(_channel_id: str, thread_ts: str, _status: str) -> bool:
        calls.append(thread_ts)
        if new_owner == "run":
            client.runs.list.return_value = [{"run_id": "new-run"}]
        else:
            store.items[anchor_key] = {"message_ts": "2.0"}
        return False

    monkeypatch.setattr(slack_thinking, "set_slack_thread_status", set_status)
    await slack_thinking.clear_slack_thinking_status_if_idle(
        client,
        "thread-1",
        "D1",
        "1.0",
        session_ts="0",
    )

    assert calls == ["1.0"]
    assert store.items[anchor_key] == {"message_ts": "1.0" if new_owner == "run" else "2.0"}


async def test_status_clear_retry_reloads_background_task_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _AnchorStore()
    anchor_key = (("slack_session_status_anchor", "D1"), "0")
    store.items[anchor_key] = {"message_ts": "1.0"}
    client = _status_client(store)
    client.threads.get.return_value = {"metadata": {"running_background_tasks": ["task-1"]}}
    set_status = AsyncMock(side_effect=[False, True])
    monkeypatch.setattr(slack_thinking, "set_slack_thread_status", set_status)

    await slack_thinking.clear_slack_thinking_status_if_idle(
        client,
        "thread-1",
        "D1",
        "1.0",
        session_ts="0",
        metadata={},
    )

    assert set_status.await_args_list == [
        call("D1", "1.0", ""),
        call("D1", "1.0", "Waiting for background tasks…"),
    ]
    assert store.items[anchor_key] == {"message_ts": "1.0"}


@pytest.mark.parametrize("already_completed", [False, True])
async def test_status_waits_for_each_run_without_polling(
    monkeypatch: pytest.MonkeyPatch, already_completed: bool
) -> None:
    waiting = asyncio.Event()
    completed = asyncio.Event()
    refreshed = asyncio.Event()
    requests: list[str] = []
    statuses: list[str] = []

    async def respond(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        requests.append(path)
        if path == "/threads/thread-1/runs/old-run/join":
            return httpx.Response(200, json={})
        if path == "/threads/thread-1/runs/current-run/join":
            waiting.set()
            await completed.wait()
            return httpx.Response(200, json={})
        if path == "/threads/thread-1":
            return httpx.Response(200, json={"metadata": {}})
        if path == "/threads/thread-1/runs":
            return httpx.Response(200, json=[])
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    async def set_status(_channel_id: str, _thread_ts: str, status: str) -> bool:
        statuses.append(status)
        if waiting.is_set() and status == "Thinking...":
            refreshed.set()
        return True

    monkeypatch.setattr(slack_thinking, "set_slack_thread_status", set_status)
    monkeypatch.setattr(slack_thinking, "_STATUS_REFRESH_SECONDS", 0.0)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(respond), base_url="http://test"
    ) as http:
        client = LangGraphClient(http)
        await slack_thinking.show_slack_thinking_status(
            client=client,
            thread_id="thread-1",
            run_id="old-run",
            channel_id="C1",
            thread_ts="1.0",
        )
        assert statuses[-1] == ""
        requests.clear()
        statuses.clear()
        if already_completed:
            completed.set()
        async with asyncio.timeout(2):
            async with asyncio.TaskGroup() as tasks:
                observer = tasks.create_task(
                    slack_thinking.show_slack_thinking_status(
                        client=client,
                        thread_id="thread-1",
                        run_id="current-run",
                        channel_id="C1",
                        thread_ts="1.0",
                    )
                )
                if not already_completed:
                    await refreshed.wait()
                    assert not observer.done()
                    assert "" not in statuses
                    assert requests[0] == "/threads/thread-1/runs/current-run/join"
                    assert set(requests[1:]) <= {"/threads/thread-1"}
                    completed.set()
        assert statuses[0] == "Thinking..."
        assert statuses[-1] == ""
        assert statuses.count("") == 1
