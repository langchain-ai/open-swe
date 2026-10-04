from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from langchain.tools import ToolRuntime
from langchain_core.messages import HumanMessage

from agent.tasks import delivery, service, store
from agent.tools import tasks

COORDINATOR = "coordinator"
WORKER = "worker"
MODEL = "openai:gpt-5.5"
TASK = store.TaskRecord(
    "task", COORDINATOR, "workspace", "Fix login", ["Login works"], True, "active", None
)
DELEGATION = store.Delegation(
    "delegation", "task", COORDINATOR, WORKER, "Fix login", MODEL, "high", "running", "run"
)
METADATA = {
    "source": "slack",
    "owner_login": "mason",
    "workspace": "workspace",
    "visibility": "private",
    "sandbox_id": "sandbox",
    "repo_owner": "owner",
    "repo_name": "repo",
    "slack_thread": {"channel_id": "channel", "thread_ts": "1.0"},
}


@asynccontextmanager
async def unlocked(_thread_id: str) -> AsyncIterator[None]:
    yield


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    value = SimpleNamespace(
        threads=SimpleNamespace(
            get=AsyncMock(return_value={"metadata": METADATA}),
            get_history=AsyncMock(return_value=[]),
        ),
        runs=SimpleNamespace(
            list=AsyncMock(return_value=[]),
            get=AsyncMock(return_value={"metadata": {}}),
            cancel_many=AsyncMock(),
        ),
    )
    monkeypatch.setattr(service, "langgraph_client", lambda: value)
    monkeypatch.setattr(delivery, "langgraph_client", lambda: value)
    monkeypatch.setattr(service, "authorized_metadata", AsyncMock(return_value=METADATA))
    monkeypatch.setattr(store, "thread_lock", unlocked)
    monkeypatch.setattr(store, "event_delivery_lock", unlocked)
    monkeypatch.setattr(store, "task_for_thread", AsyncMock(return_value=TASK))
    monkeypatch.setattr(store, "delegation_for_worker", AsyncMock(return_value=DELEGATION))

    async def record(
        worker_thread_id: str, *, dispatch_key: str, content: str
    ) -> store.WorkerDispatch:
        return store.WorkerDispatch(dispatch_key, worker_thread_id, content, None, False)

    monkeypatch.setattr(store, "record_worker_dispatch", record)
    monkeypatch.setattr(store, "register_worker_dispatch", AsyncMock())
    monkeypatch.setattr(store, "settle_worker_dispatch", AsyncMock())
    monkeypatch.setattr(store, "cancel_pending_dispatches", AsyncMock())
    monkeypatch.setattr(store, "pending_worker_dispatches", AsyncMock(return_value=[]))
    monkeypatch.setattr(store, "task_dispatch_invocation", AsyncMock(return_value=None))
    return value


async def test_spawn_retries_dispatch_without_creating_another_worker(
    client: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    pending: store.Delegation | None = None
    creation_count = 0
    dispatched: list[str] = []

    async def lookup(_worker_id: str) -> store.Delegation | None:
        return pending

    async def create(
        coordinator_thread_id: str,
        *,
        worker_thread_id: str,
        instructions: str,
        model: str | None,
        effort: str | None,
    ) -> store.Delegation:
        nonlocal pending, creation_count
        creation_count += 1
        pending = replace(
            DELEGATION, worker_thread_id=worker_thread_id, status="pending", run_id=None
        )
        return pending

    async def start(delegation: store.Delegation) -> str:
        dispatched.append(delegation.worker_thread_id)
        if len(dispatched) == 1:
            raise ConnectionError("response lost")
        return "run"

    monkeypatch.setattr(store, "delegation_for_worker", lookup)
    monkeypatch.setattr(store, "create_delegation", create)
    monkeypatch.setattr(service, "model_choice", AsyncMock(return_value=(MODEL, "high")))
    monkeypatch.setattr(service, "ensure_worker_thread", AsyncMock())
    monkeypatch.setattr(service, "dispatch_worker", start)
    kwargs = {
        "instructions": "Fix login",
        "model": MODEL,
        "effort": "high",
        "request_id": "tool-call",
    }
    with pytest.raises(ConnectionError):
        await service.spawn_worker(service.Actor(COORDINATOR, "mason"), **kwargs)
    result = await service.spawn_worker(service.Actor(COORDINATOR, "mason"), **kwargs)
    assert creation_count == 1
    assert dispatched == [result.worker_thread_id, result.worker_thread_id]


async def test_worker_creation_shares_sandbox_but_not_user_delivery_context(
    client: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    created: dict[str, object] = {}

    async def create(
        _client: object, thread_id: str, *, title: str, metadata: dict[str, object], if_exists: str
    ) -> None:
        created.update(metadata)
        client.threads.get.return_value = {"metadata": metadata}

    monkeypatch.setattr(service, "create_thread", create)
    await service.ensure_worker_thread(DELEGATION, METADATA)
    assert created["sandbox_id"] == "sandbox"
    assert created["sandbox_host_thread_id"] == COORDINATOR
    assert created["owner_login"] == "mason"
    assert created["visibility"] == "private"
    assert created["workspace"] == "workspace"
    assert created["source"] == "dashboard"
    assert "slack_thread" not in created
    assert "pr" not in created


async def test_first_spawn_rejects_running_background_commands_before_persistence(
    client: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        service,
        "authorized_metadata",
        AsyncMock(return_value={**METADATA, "running_background_tasks": ["command"]}),
    )
    monkeypatch.setattr(store, "delegation_for_worker", AsyncMock(return_value=None))
    persist = AsyncMock()
    monkeypatch.setattr(store, "create_delegation", persist)
    with pytest.raises(ValueError, match="background commands"):
        await service.spawn_worker(
            service.Actor(COORDINATOR, "mason"),
            instructions="Fix login",
            model=MODEL,
            effort="high",
            request_id="tool-call",
        )
    persist.assert_not_awaited()


async def test_worker_cannot_control_sibling_even_with_user_credentials(
    client: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    dispatch = AsyncMock()
    monkeypatch.setattr(service, "dispatch_worker", dispatch)
    with pytest.raises(PermissionError, match="permanent coordinator"):
        await service.message_worker(
            service.Actor(WORKER, "mason"),
            worker_thread_id="sibling",
            message="Do something",
            request_id="call",
        )
    dispatch.assert_not_awaited()


@pytest.mark.parametrize("operation", ["spawn", "message"])
async def test_public_thread_participant_cannot_launch_with_owner_credentials(
    client: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    persist = AsyncMock()
    monkeypatch.setattr(store, "create_delegation", persist)
    runtime = ToolRuntime(
        state={
            "messages": [
                HumanMessage(
                    content='<input-message sender="github:another-user" kind="human">Help</input-message>'
                )
            ]
        },
        context=None,
        config={
            "configurable": {
                "thread_id": COORDINATOR,
                "github_login": "mason",
                "user_email": "mason@example.com",
            }
        },
        stream_writer=lambda _: None,
        tool_call_id="call",
        store=None,
    )
    assert tasks._actor(runtime).email is None
    with pytest.raises(PermissionError, match="owner"):
        if operation == "spawn":
            await tasks.spawn_worker("Fix login", runtime, model=MODEL, effort="high")
        else:
            await tasks.message_worker(WORKER, "Fix login", runtime)
    persist.assert_not_awaited()


async def test_explicit_follow_up_retry_does_not_duplicate_or_interrupt_work(
    client: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[str, dict[str, object]]] = []

    async def create(thread_id: str, assistant_id: str, **kwargs: object) -> dict[str, str]:
        calls.append((thread_id, kwargs))
        client.runs.list.return_value = [
            {
                "run_id": "follow-up",
                "metadata": {"task_dispatch_key": "task-message:coordinator:call"},
            }
        ]
        return {"run_id": "follow-up"}

    monkeypatch.setattr(service, "create_durable_run", create)
    monkeypatch.setattr(
        store,
        "set_delegation_run",
        AsyncMock(side_effect=[ConnectionError("lost registration"), None]),
    )
    with pytest.raises(ConnectionError):
        await service.message_worker(
            service.Actor(COORDINATOR, "mason"),
            worker_thread_id=WORKER,
            message="Also test the reset path",
            request_id="call",
        )
    result = await service.message_worker(
        service.Actor(COORDINATOR, "mason"),
        worker_thread_id=WORKER,
        message="Also test the reset path",
        request_id="call",
    )
    assert result == "follow-up"
    assert len(calls) == 1
    assert calls[0][0] == WORKER
    assert calls[0][1]["multitask_strategy"] == "enqueue"
    assert calls[0][1]["task_worker_dispatch"] is True
    client.runs.cancel_many.assert_not_awaited()


async def test_delivery_recovers_after_mark_failure_without_duplicate_run(
    client: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    event = store.TaskEvent("event", "task", WORKER, "completed", "The test passed", False)
    sent: list[str] = []
    exists = False

    async def lookup(_client: object, thread_id: str, key: str) -> str | None:
        return "notification" if exists else None

    async def create(thread_id: str, assistant_id: str, **kwargs: object) -> dict[str, str]:
        nonlocal exists
        exists = True
        sent.append(thread_id)
        assert kwargs["multitask_strategy"] == "enqueue"
        return {"run_id": "notification"}

    monkeypatch.setattr(store, "pending_events", AsyncMock(return_value=[event]))
    monkeypatch.setattr(delivery, "dispatched_run", lookup)
    monkeypatch.setattr(delivery, "create_durable_run", create)
    monkeypatch.setattr(
        store,
        "mark_event_delivered",
        AsyncMock(side_effect=[ConnectionError("database unavailable"), None]),
    )
    with pytest.raises(ConnectionError):
        await delivery.deliver_events(COORDINATOR)
    await delivery.deliver_events(COORDINATOR)
    assert sent == [COORDINATOR]


async def test_cancellation_does_not_claim_worker_has_stopped_early(
    client: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    client.runs.list.side_effect = [[{"run_id": "queued"}], [{"run_id": "running"}]]
    finish = AsyncMock()
    record = AsyncMock()
    monkeypatch.setattr(store, "finish_delegation", finish)
    monkeypatch.setattr(store, "record_event", record)
    monkeypatch.setattr(service, "relay_events", AsyncMock())
    await service.cancel_worker(service.Actor(COORDINATOR, "mason"), worker_thread_id=WORKER)
    client.runs.cancel_many.assert_awaited_once_with(
        thread_id=WORKER, run_ids=["queued", "running"], action="interrupt"
    )
    finish.assert_not_awaited()
    assert record.await_args.kwargs["kind"] == "cancellation_requested"


async def test_reconciliation_recovers_missed_failure_even_after_a_follow_up_finished(
    client: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[tuple[str, str]] = []
    pending = [
        store.WorkerDispatch("first", WORKER, "Fix login", "first-run", False),
        store.WorkerDispatch("second", WORKER, "Test reset", "second-run", False),
    ]

    @asynccontextmanager
    async def connection() -> AsyncIterator[SimpleNamespace]:
        yield SimpleNamespace(execute=AsyncMock(return_value=[]))

    async def get_run(thread_id: str, run_id: str) -> dict[str, object]:
        return {"status": "error" if run_id == "first-run" else "success", "metadata": {}}

    async def record(worker_thread_id: str, *, event_key: str, kind: str, content: str) -> None:
        events.append((event_key, kind))

    async def settle(worker_thread_id: str, run_id: str) -> None:
        pending[:] = [item for item in pending if item.run_id != run_id]

    monkeypatch.setattr(delivery.postgres, "configured", lambda: True)
    monkeypatch.setattr(delivery.postgres, "connection", connection)
    monkeypatch.setattr(
        store, "pending_worker_dispatches", AsyncMock(side_effect=lambda: list(pending))
    )
    monkeypatch.setattr(store, "coordinators_with_pending_events", AsyncMock(return_value=[]))
    monkeypatch.setattr(store, "record_event", record)
    monkeypatch.setattr(store, "settle_worker_dispatch", settle)
    monkeypatch.setattr(store, "finish_delegation", AsyncMock())
    monkeypatch.setattr(delivery, "worker_result", AsyncMock(return_value="Result"))
    monkeypatch.setattr(delivery, "deliver_events", AsyncMock(return_value=0))
    client.runs.get.side_effect = get_run
    result = await delivery.reconcile_tasks()
    assert result["task_workers_reconciled"] == 2
    assert events == [("completion:first-run", "failed"), ("completion:second-run", "completed")]
    assert pending == []


async def test_result_is_read_from_the_completed_invocation_not_the_newer_checkpoint(
    client: SimpleNamespace,
) -> None:
    client.runs.get.return_value = {
        "metadata": {"task_dispatch_key": "first", "invocation_id": "original"}
    }

    async def history(
        thread_id: str, *, limit: int, metadata: dict[str, str]
    ) -> list[dict[str, object]]:
        if metadata == {"invocation_id": "original"}:
            return [{"values": {"messages": [{"type": "ai", "content": "Login passes"}]}}]
        return []

    client.threads.get_history.side_effect = history
    assert await delivery.worker_result(WORKER, "first-run", "success", None) == "Login passes"


@pytest.mark.parametrize("order", [("duplicate", "original"), ("original", "duplicate")])
@pytest.mark.parametrize("status,kind", [("success", "completed"), ("interrupted", "cancelled")])
async def test_duplicate_callbacks_preserve_claimed_invocation_result(
    client: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
    order: tuple[str, str],
    status: str,
    kind: str,
) -> None:
    runs = {
        name: {
            "run_id": name,
            "metadata": {"task_dispatch_key": "dispatch", "invocation_id": name},
        }
        for name in order
    }
    current = replace(DELEGATION, run_id="original")
    events: list[tuple[str, str, str]] = []
    settled: list[str] = []

    async def finish(
        worker_id: str, *, status: store.TerminalDelegationStatus, run_id: str
    ) -> None:
        nonlocal current
        current = replace(current, status=status, run_id=run_id)

    async def record(worker_id: str, *, event_key: str, kind: str, content: str) -> None:
        events.append((event_key, kind, content))

    client.runs.get.side_effect = lambda thread_id, run_id: runs[run_id]
    client.runs.list.return_value = [runs["duplicate"], runs["original"]]
    monkeypatch.setattr(store, "task_dispatch_invocation", AsyncMock(return_value="original"))
    monkeypatch.setattr(store, "delegation_for_worker", AsyncMock(side_effect=lambda _: current))
    register = AsyncMock()
    monkeypatch.setattr(delivery, "register_dispatch_run", register)
    monkeypatch.setattr(store, "finish_delegation", finish)
    monkeypatch.setattr(store, "record_event", record)
    monkeypatch.setattr(
        store,
        "settle_worker_dispatch",
        AsyncMock(side_effect=lambda _, run_id: settled.append(run_id)),
    )
    result = AsyncMock(return_value="The canonical worker result")
    monkeypatch.setattr(delivery, "worker_result", result)
    monkeypatch.setattr(delivery, "deliver_events", AsyncMock(return_value=1))
    for run_id in order:
        callback_status = status if run_id == "original" else "success"
        assert await delivery.handle_worker_completion(WORKER, run_id, callback_status)
    assert events == [("completion:original", kind, "The canonical worker result")]
    assert (current.run_id, current.status) == ("original", kind)
    assert settled == ["original"]
    register.assert_awaited_once_with(client, WORKER, "dispatch", "original")
    result.assert_awaited_once_with(WORKER, "original", status, None)
    assert await service.dispatched_run(client, WORKER, "dispatch") == "original"
    assert await service.is_duplicate_task_dispatch(COORDINATOR, runs["duplicate"])


@pytest.mark.parametrize("registration_conflict", [False, True])
async def test_failure_before_receipt_claim_is_delivered(
    client: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, registration_conflict: bool
) -> None:
    client.runs.get.return_value = {
        "metadata": {"task_dispatch_key": "dispatch", "invocation_id": "failed-before-agent"}
    }
    record = AsyncMock()
    finish = AsyncMock()
    monkeypatch.setattr(store, "record_event", record)
    monkeypatch.setattr(store, "finish_delegation", finish)
    monkeypatch.setattr(delivery, "deliver_events", AsyncMock(return_value=1))
    if registration_conflict:
        monkeypatch.setattr(
            store,
            "register_worker_dispatch",
            AsyncMock(side_effect=ValueError("Already registered")),
        )
    assert await delivery.handle_worker_completion(WORKER, "run", "error", error="Factory failed")
    assert record.await_args.kwargs["kind"] == "failed"
    assert "Factory failed" in record.await_args.kwargs["content"]
    if registration_conflict:
        finish.assert_not_awaited()
        store.settle_worker_dispatch.assert_not_awaited()
    else:
        finish.assert_awaited_once_with(WORKER, status="failed", run_id="run")


async def test_reconciliation_repairs_duplicate_registration_from_receipt(
    client: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    intent = store.WorkerDispatch("dispatch", WORKER, "Fix login", "duplicate", False)
    repaired: list[str] = []
    runs = [
        {"run_id": name, "metadata": {"task_dispatch_key": "dispatch", "invocation_id": name}}
        for name in ("duplicate", "original")
    ]

    @asynccontextmanager
    async def connection() -> AsyncIterator[SimpleNamespace]:
        yield SimpleNamespace(execute=AsyncMock(return_value=[]))

    client.runs.list.return_value = runs
    client.runs.get.return_value = {**runs[1], "status": "running"}
    monkeypatch.setattr(delivery.postgres, "configured", lambda: True)
    monkeypatch.setattr(delivery.postgres, "connection", connection)
    monkeypatch.setattr(store, "pending_worker_dispatches", AsyncMock(return_value=[intent]))
    monkeypatch.setattr(store, "task_dispatch_invocation", AsyncMock(return_value="original"))
    monkeypatch.setattr(store, "coordinators_with_pending_events", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        delivery,
        "register_dispatch_run",
        AsyncMock(side_effect=lambda _client, _worker, _key, run_id: repaired.append(run_id)),
    )
    await delivery.reconcile_tasks()
    assert repaired == ["original"]
    client.runs.get.assert_awaited_once_with(WORKER, "original")
