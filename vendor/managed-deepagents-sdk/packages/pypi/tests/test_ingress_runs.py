"""Transport-neutral run lifecycle with an injected Agent Server client."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from managed_deepagents import _ingress_runs as ingress_runs
from managed_deepagents._channels.types import ChannelError
from managed_deepagents._thread_record import next_thread_record, thread_record_metadata


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    for name in (
        "LANGCHAIN_API_KEY",
        "LANGSMITH_API_KEY",
        "MDA_INGRESS_SECRET",
        "MDA_HANDOFF_SECRET",
    ):
        monkeypatch.delenv(name, raising=False)
    return SimpleNamespace(
        threads=SimpleNamespace(create=AsyncMock()),
        runs=SimpleNamespace(
            create=AsyncMock(return_value={"run_id": "run-1"}),
            join=AsyncMock(return_value={"messages": []}),
            get=AsyncMock(return_value={"status": "success"}),
        ),
    )


@pytest.mark.parametrize("resume", [False, True])
@pytest.mark.parametrize("with_options", [False, True])
def test_injected_client_preserves_payload_and_options(
    client: SimpleNamespace, resume: bool, with_options: bool
) -> None:
    messages = [{"role": "user", "content": [{"type": "text", "text": "hello"}]}]
    command = {"interrupt-1": {"decisions": [{"type": "approve"}]}}
    context = {"provider": "custom", "event": {"kind": "message"}, "custom": True}
    config = {"configurable": {"custom": "value"}}
    options = {"context": context, "config": config} if with_options else {}
    if resume:
        pending = ingress_runs.resume_ingress_run(
            client=client, thread_id="thread-1", assistant_id="agent", resume=command, **options
        )
    else:
        pending = ingress_runs.start_ingress_run(
            client=client, thread_id="thread-1", assistant_id="agent", messages=messages, **options
        )

    assert asyncio.run(pending) == {"thread_id": "thread-1", "run_id": "run-1"}
    if resume:
        client.threads.create.assert_not_awaited()
    else:
        client.threads.create.assert_awaited_once_with(thread_id="thread-1", if_exists="do_nothing")
    payload = {"command": {"resume": command}} if resume else {"input": {"messages": messages}}
    client.runs.create.assert_awaited_once_with(
        "thread-1", "agent", **payload, **options, multitask_strategy="enqueue"
    )
    if with_options:
        assert client.runs.create.call_args.kwargs["context"] is context
        assert client.runs.create.call_args.kwargs["config"] is config


def test_thread_rejection_stops_run_creation(client: SimpleNamespace) -> None:
    client.threads.create.side_effect = RuntimeError("thread access denied")
    with pytest.raises(RuntimeError, match="thread access denied"):
        asyncio.run(
            ingress_runs.start_ingress_run(
                client=client, thread_id="thread-1", assistant_id="agent", messages=[]
            )
        )
    client.runs.create.assert_not_awaited()


@pytest.mark.parametrize("resume", [False, True])
@pytest.mark.parametrize("response", [{}, {"run_id": " "}, {"run_id": 5}, None])
def test_run_creation_requires_run_id(
    client: SimpleNamespace, resume: bool, response: object
) -> None:
    client.runs.create.return_value = response
    if resume:
        pending = ingress_runs.resume_ingress_run(
            client=client, thread_id="thread-1", assistant_id="agent", resume={}
        )
    else:
        pending = ingress_runs.start_ingress_run(
            client=client, thread_id="thread-1", assistant_id="agent", messages=[]
        )
    with pytest.raises(ChannelError, match="did not return run_id"):
        asyncio.run(pending)


@pytest.mark.parametrize("status", ["success", "error", "timeout", "interrupted"])
def test_observe_joins_before_reading_terminal_status(client: SimpleNamespace, status: str) -> None:
    values = {"messages": [{"role": "assistant", "content": "done"}]}

    async def get(_thread_id: str, _run_id: str) -> dict[str, str]:
        client.runs.join.assert_awaited_once_with("thread-1", "run-1")
        return {"status": status}

    client.runs.join.return_value = values
    client.runs.get.side_effect = get
    observed = asyncio.run(
        ingress_runs.observe_ingress_run(client=client, thread_id="thread-1", run_id="run-1")
    )
    assert observed == {"values": values, "status": status}
    assert observed["values"] is values
    client.runs.get.assert_awaited_once_with("thread-1", "run-1")


@pytest.mark.parametrize("response", [{"status": "pending"}, {}, None])
def test_observe_leaves_status_policy_to_transport(
    client: SimpleNamespace, response: object
) -> None:
    client.runs.get.return_value = response
    observed = asyncio.run(
        ingress_runs.observe_ingress_run(client=client, thread_id="thread-1", run_id="run-1")
    )
    assert observed["status"] == (response.get("status") if isinstance(response, dict) else None)


class _LookupError(Exception):
    def __init__(self, status_code: int) -> None:
        super().__init__(f"HTTP {status_code}")
        self.response = SimpleNamespace(status_code=status_code)


def _read(
    client: SimpleNamespace, delivery_key: str | None = None
) -> ingress_runs.IngressThreadState:
    return asyncio.run(
        ingress_runs.ingress_thread_state(
            client=client, thread_id="thread-1", delivery_key=delivery_key
        )
    )


def _state(client: SimpleNamespace, delivery_key: str | None = None) -> str:
    return _read(client, delivery_key)["state"]


def test_a_missing_thread_is_ready_and_other_failures_propagate(
    client: SimpleNamespace,
) -> None:
    client.runs.list = AsyncMock(return_value=[])
    client.threads.get = AsyncMock(side_effect=_LookupError(404))
    assert _state(client, "d-1") == "ready"
    client.runs.list.assert_not_awaited()

    client.threads.get = AsyncMock(side_effect=_LookupError(503))
    with pytest.raises(_LookupError):
        _state(client)


def test_a_delivery_is_found_among_the_threads_recent_runs(client: SimpleNamespace) -> None:
    client.threads.get = AsyncMock(return_value={"status": "idle"})
    client.runs.list = AsyncMock(
        return_value=[{"metadata": {"mda_delivery_key": "d-1"}}, {"metadata": {}}]
    )
    assert _state(client, "d-1") == "duplicate"
    client.runs.list.assert_awaited_with("thread-1", limit=100)
    assert _state(client, "d-2") == "ready"
    # Without a delivery key nothing is looked up.
    client.runs.list.reset_mock()
    assert _state(client) == "ready"
    client.runs.list.assert_not_awaited()


def test_a_delivery_recorded_on_the_thread_is_a_duplicate(client: SimpleNamespace) -> None:
    client.threads.get = AsyncMock(
        return_value={"status": "busy", "metadata": {"mda_delivery_keys": ["d-1", 7]}}
    )
    client.runs.list = AsyncMock(return_value=[])
    # `rollback` deleted the run, so only the thread knows the delivery.
    assert _state(client, "d-1") == "duplicate"
    client.runs.list.assert_not_awaited()
    assert _state(client, "d-2") == "busy"


def test_recording_a_delivery_keeps_the_latest_keys(client: SimpleNamespace) -> None:
    older = [f"d-{index}" for index in range(100)]
    client.threads.get = AsyncMock(
        return_value={"metadata": {"assistant_id": "agent", "mda_delivery_keys": [*older, 3]}}
    )
    client.threads.update = AsyncMock()
    record = ingress_runs.record_thread_delivery
    asyncio.run(record(client=client, thread_id="thread-1", delivery_key="d-0"))
    client.threads.update.assert_awaited_with(
        "thread-1", metadata={"mda_delivery_keys": [*older[1:], "d-0"]}
    )

    client.threads.get = AsyncMock(return_value={"metadata": {}})
    asyncio.run(record(client=client, thread_id="thread-1", delivery_key="d-new"))
    client.threads.update.assert_awaited_with("thread-1", metadata={"mda_delivery_keys": ["d-new"]})


@pytest.mark.parametrize(
    ("thread", "state"),
    [
        ({"status": "idle", "interrupts": {"task": [{"id": "i-1", "value": {}}]}}, "paused"),
        # A cancelled run leaves the thread ``interrupted`` with nothing to answer.
        ({"status": "interrupted"}, "ready"),
        ({"status": "idle", "interrupts": {}}, "ready"),
        ({"status": "busy", "interrupts": {}}, "busy"),
    ],
)
def test_a_thread_waiting_on_an_interrupt_is_paused(
    client: SimpleNamespace, thread: dict[str, object], state: str
) -> None:
    client.threads.get = AsyncMock(return_value=thread)
    assert _state(client) == state


def test_one_thread_read_gives_its_assistant(client: SimpleNamespace) -> None:
    client.threads.get = AsyncMock(
        return_value={
            "status": "idle",
            "metadata": {"assistant_id": "a-1"},
            "interrupts": {"task": [{"id": "i-1", "value": "Which branch?"}]},
        }
    )
    assert _read(client) == {
        "state": "paused",
        "assistant_id": "a-1",
        "interrupts": [{"id": "i-1", "value": "Which branch?"}],
    }
    client.threads.get.assert_awaited_once()


def test_a_run_is_tagged_with_its_delivery(client: SimpleNamespace) -> None:
    asyncio.run(
        ingress_runs.start_ingress_run(
            client=client,
            thread_id="thread-1",
            assistant_id="agent",
            messages=[],
            metadata={"mda_delivery_key": "d-1"},
        )
    )
    assert client.runs.create.await_args.kwargs["metadata"] == {"mda_delivery_key": "d-1"}


def test_waiting_joins_queued_and_running_runs_until_none_is_left(
    client: SimpleNamespace,
) -> None:
    client.runs.list = AsyncMock(
        side_effect=[
            [
                {"run_id": "queued", "status": "pending"},
                {"run_id": "active", "status": "running"},
                {"run_id": "old", "status": "success"},
            ],
            [{"run_id": "queued", "status": "running"}, {"run_id": "active", "status": "success"}],
            [{"run_id": "queued", "status": "interrupted"}],
        ]
    )
    asyncio.run(ingress_runs.wait_for_idle_thread(client=client, thread_id="thread-1"))
    assert [call.args for call in client.runs.join.await_args_list] == [
        ("thread-1", "active"),
        ("thread-1", "queued"),
    ]


def test_cancel_cancels_the_threads_active_runs(client: SimpleNamespace) -> None:
    client.runs.list = AsyncMock(
        return_value=[
            {"run_id": "run-3", "status": "pending"},
            {"run_id": "run-2", "status": "running"},
            {"run_id": "run-1", "status": "success"},
        ]
    )
    client.runs.cancel = AsyncMock()
    cancel = ingress_runs.cancel_ingress_runs
    assert asyncio.run(cancel(client=client, thread_id="thread-1")) == ["run-3", "run-2"]
    client.runs.list.assert_awaited_once_with("thread-1", limit=20)
    assert [call.args for call in client.runs.cancel.await_args_list] == [
        ("thread-1", "run-3"),
        ("thread-1", "run-2"),
    ]
    assert client.runs.cancel.await_args.kwargs == {"wait": False, "action": "interrupt"}


def test_cancel_returns_only_runs_it_cancelled(client: SimpleNamespace) -> None:
    cancel = ingress_runs.cancel_ingress_runs
    client.runs.list = AsyncMock(side_effect=_LookupError(404))
    assert asyncio.run(cancel(client=client, thread_id="thread-1")) == []

    active = [{"run_id": "run-2", "status": "running"}, {"run_id": "run-1", "status": "pending"}]
    client.runs.list = AsyncMock(return_value=active)
    # A run that finished meanwhile is not cancelled.
    client.runs.cancel = AsyncMock(side_effect=[_LookupError(409), None])
    assert asyncio.run(cancel(client=client, thread_id="thread-1")) == ["run-1"]

    client.runs.cancel = AsyncMock(side_effect=_LookupError(503))
    with pytest.raises(_LookupError):
        asyncio.run(cancel(client=client, thread_id="thread-1"))


_BINDING: Any = {
    "name": "slack",
    "provider": "slack",
    "source_thread_key": "slack:v1:A1:T1:C1:171.1",
    "delivery_id": "delivery-1",
    "transport": "trigger_server",
    "event": {"type": "message", "messages": []},
    "target": {"channel_id": "c", "address": {}, "source_thread_key": "slack:v1:A1:T1:C1:171.1"},
}


def _record(*senders: str) -> dict[str, Any]:
    record = None
    for sender in senders:
        record = next_thread_record(record, _BINDING, sender)
    return thread_record_metadata(cast("Any", record))


def _client(existing: dict[str, object] | None) -> SimpleNamespace:
    """A thread that keeps ``existing`` metadata, as ``do_nothing`` does."""

    async def create(**kwargs: Any) -> dict[str, object]:
        return {"metadata": kwargs.get("metadata") if existing is None else existing}

    return SimpleNamespace(
        threads=SimpleNamespace(
            create=AsyncMock(side_effect=create),
            get=AsyncMock(return_value={"metadata": existing or {}}),
            update=AsyncMock(),
        ),
        runs=SimpleNamespace(create=AsyncMock(return_value={"run_id": "run-1"})),
    )


@pytest.mark.parametrize(
    ("existing", "sender", "updated"),
    [
        # A new thread is created with its record.
        (None, "alice", None),
        # An existing thread gets the sender as a member.
        (_record("alice"), "bob", _record("alice", "bob")),
        # A service sender adds no participant.
        (None, "mda:channel", None),
    ],
    ids=["new", "existing", "service"],
)
def test_start_writes_the_thread_record_before_the_run(
    existing: Any, sender: str, updated: Any
) -> None:
    client = _client(existing)
    asyncio.run(
        ingress_runs.start_ingress_run(
            client=cast("Any", client),
            thread_id="thread-1",
            assistant_id="agent",
            messages=[],
            thread_origin={"binding": _BINDING, "sender": sender},
        )
    )
    assert client.threads.create.await_args.kwargs["metadata"] == _record(sender)
    if updated is None:
        client.threads.update.assert_not_awaited()
    else:
        client.threads.update.assert_awaited_once_with("thread-1", metadata=updated)


def test_resume_updates_the_record_before_the_run() -> None:
    client = _client({"owner": "conversation"})
    order: list[str] = []
    client.threads.update.side_effect = lambda *_a, **_k: order.append("update")
    client.runs.create.side_effect = lambda *_a, **_k: order.append("run") or {"run_id": "r"}
    asyncio.run(
        ingress_runs.resume_ingress_run(
            client=cast("Any", client),
            thread_id="thread-1",
            assistant_id="agent",
            resume={"interrupt-1": "yes"},
            thread_origin={"binding": _BINDING, "sender": "alice"},
        )
    )
    assert order == ["update", "run"]
    assert client.threads.update.await_args.kwargs["metadata"] == _record("alice")


def test_hands_the_trace_id_to_the_run_and_records_its_ids_and_status(
    client: SimpleNamespace,
) -> None:
    from managed_deepagents._telemetry.otel import (  # noqa: PLC0415
        LogContext,
        enter_log_context,
        exit_log_context,
    )

    trace_id = "4bf92f3577b34da6a3ce929d0e0e4736"
    context = LogContext(trace_id=trace_id)

    async def run() -> None:
        token = enter_log_context(context)
        try:
            started = await ingress_runs.start_ingress_run(
                client=client,  # type: ignore[arg-type]
                thread_id="thread-1",
                assistant_id="agent",
                messages=[],
            )
            await ingress_runs.observe_ingress_run(client=client, **started)  # type: ignore[arg-type]
        finally:
            exit_log_context(token)

    asyncio.run(run())

    assert client.runs.create.await_args.kwargs["metadata"] == {"mda_trace_id": trace_id}
    assert context.attributes == {
        "thread_id": "thread-1",
        "run_id": "run-1",
        "run_status": "success",
    }


def test_keeps_a_runs_own_metadata_beside_the_trace_id(client: SimpleNamespace) -> None:
    from managed_deepagents._telemetry.otel import (  # noqa: PLC0415
        LogContext,
        enter_log_context,
        exit_log_context,
    )

    trace_id = "4bf92f3577b34da6a3ce929d0e0e4736"

    async def run() -> None:
        token = enter_log_context(LogContext(trace_id=trace_id))
        try:
            await ingress_runs.start_ingress_run(
                client=client,  # type: ignore[arg-type]
                thread_id="thread-1",
                assistant_id="agent",
                messages=[],
                metadata={"mda_delivery_key": "d-1"},
            )
        finally:
            exit_log_context(token)

    asyncio.run(run())

    assert client.runs.create.await_args.kwargs["metadata"] == {
        "mda_delivery_key": "d-1",
        "mda_trace_id": trace_id,
    }


def test_hands_the_run_the_deliverys_event_type_and_user_resolution(
    client: SimpleNamespace,
) -> None:
    from managed_deepagents._telemetry.otel import (  # noqa: PLC0415
        LogContext,
        enter_log_context,
        exit_log_context,
        set_log_attributes,
    )

    trace_id = "4bf92f3577b34da6a3ce929d0e0e4736"

    async def run() -> None:
        token = enter_log_context(LogContext(trace_id=trace_id))
        try:
            set_log_attributes(
                {"provider_event_type": "issues.opened", "user_resolution": "channel"}
            )
            await ingress_runs.start_ingress_run(
                client=client,  # type: ignore[arg-type]
                thread_id="thread-1",
                assistant_id="agent",
                messages=[],
            )
        finally:
            exit_log_context(token)

    asyncio.run(run())

    metadata = client.runs.create.await_args.kwargs["metadata"]
    assert metadata == {
        "mda_trace_id": trace_id,
        "mda_provider_event_type": "issues.opened",
        "mda_user_resolution": "channel",
    }
    assert ingress_runs.ingress_run_origin(metadata) == {
        "provider_event_type": "issues.opened",
        "user_resolution": "channel",
    }
    assert ingress_runs.ingress_run_origin(None) == {
        "provider_event_type": None,
        "user_resolution": None,
    }


def test_sends_no_metadata_outside_a_log_context(client: SimpleNamespace) -> None:
    asyncio.run(
        ingress_runs.start_ingress_run(
            client=client,  # type: ignore[arg-type]
            thread_id="thread-1",
            assistant_id="agent",
            messages=[],
        )
    )

    assert "metadata" not in client.runs.create.await_args.kwargs
