from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import update

from agent import completion
from agent.database import postgres
from agent.tasks import events, store
from agent.webhooks import event_matches
from agent.webhooks.event_matches import EventMatch

_WORKER = "worker"
_COORDINATOR = "coordinator"


@pytest.fixture
def worker_context(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    context = SimpleNamespace(
        task=SimpleNamespace(
            id=uuid4(),
            coordinator_thread_id=_COORDINATOR,
            status="active",
            acceptance_criteria=["Login succeeds"],
        ),
        membership=SimpleNamespace(role="worker"),
    )
    monkeypatch.setattr(store, "load_context", AsyncMock(return_value=context))
    monkeypatch.setattr(
        store, "get_delegation", AsyncMock(return_value=SimpleNamespace(cancelled=False))
    )
    monkeypatch.setattr(events.EventSubscription, "deliver_to", AsyncMock())
    return context


async def test_result_uses_completed_payload_and_invocation_not_newer_thread_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = SimpleNamespace(threads=AsyncMock(), runs=AsyncMock())
    client.threads.get_state.return_value = {
        "values": {"messages": [{"type": "ai", "content": "Unrelated newer result"}]}
    }

    async def history(
        thread_id: str, *, limit: int, metadata: dict[str, str]
    ) -> list[dict[str, object]]:
        assert metadata == {"invocation_id": "completed-invocation"}
        return [{"values": {"messages": [{"type": "ai", "content": "Login passes"}]}}]

    client.threads.get_history.side_effect = history
    monkeypatch.setattr(events, "langgraph_client", lambda: client)
    payload = {
        "metadata": {"invocation_id": "completed-invocation"},
        "values": {
            "messages": [{"type": "ai", "content": [{"type": "text", "text": "Reset passes"}]}]
        },
    }
    assert await events.worker_result(_WORKER, "old-run", "success", payload) == "Reset passes"
    assert (
        await events.worker_result(_WORKER, "old-run", "success", {"metadata": payload["metadata"]})
        == "Login passes"
    )
    client.threads.get_state.assert_not_awaited()


@pytest.mark.parametrize(
    ("status", "payload", "expected"),
    [
        (
            "success",
            {"values": {"messages": [{"type": "ai", "content": "Login passes"}]}},
            "Login passes",
        ),
        (
            "error",
            {"error": {"error": "SandboxGoneError", "message": "Shared sandbox deleted"}},
            "Shared sandbox deleted",
        ),
    ],
)
async def test_worker_outcome_returns_to_coordinator_without_completing_task(
    worker_context: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
    status: str,
    payload: dict[str, object],
    expected: str,
) -> None:
    from agent.tasks import service

    notify = AsyncMock()
    monkeypatch.setattr(service, "notify", notify)
    assert await events.worker_finished(_WORKER, "run", status, payload)
    task, recipient, delivery_id, content = notify.await_args.args
    assert recipient == _COORDINATOR
    assert delivery_id == "finished:worker:run"
    assert expected in content
    assert _WORKER in content
    assert task.status == worker_context.task.status == "active"


async def test_persistence_failure_fails_webhook_after_usage_and_transcript_settlement(
    worker_context: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agent.tasks import service

    finalized = AsyncMock()
    settled = AsyncMock()
    monkeypatch.setattr(completion, "_finalize_agent_usage_telemetry", finalized)
    monkeypatch.setattr(completion, "_settle_transcript_turn", settled)
    monkeypatch.setattr(service, "notify", AsyncMock(side_effect=ConnectionError("database down")))
    with pytest.raises(ConnectionError, match="database down"):
        await completion.handle_run_completion(
            {
                "thread_id": _WORKER,
                "run_id": "run",
                "status": "error",
                "error": "Factory failed",
            }
        )
    finalized.assert_awaited_once()
    settled.assert_awaited_once()


async def test_cancelled_worker_reports_outcome_without_restarting_owed_assignment(
    worker_context: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agent.tasks import service

    monkeypatch.setattr(
        store, "get_delegation", AsyncMock(return_value=SimpleNamespace(cancelled=True))
    )
    notify = AsyncMock()
    monkeypatch.setattr(service, "notify", notify)
    monkeypatch.setattr(completion, "_finalize_agent_usage_telemetry", AsyncMock())
    monkeypatch.setattr(completion, "_settle_transcript_turn", AsyncMock())
    result = await completion.handle_run_completion(
        {"thread_id": _WORKER, "run_id": "run", "status": "error", "error": "Cancelled"}
    )
    assert result["status"] == "ok"
    notify.assert_awaited_once()
    events.EventSubscription.deliver_to.assert_not_awaited()


@pytest.mark.parametrize("busy", [False, True])
async def test_duplicate_completion_delivers_one_durable_result_to_idle_or_busy_coordinator(
    registry_db: None, monkeypatch: pytest.MonkeyPatch, busy: bool
) -> None:
    from agent.tasks import service

    task = await store.configure(
        _COORDINATOR,
        title="Repair login",
        acceptance_criteria=["Login succeeds"],
        workspace="default",
    )
    await store.reserve_worker(
        task.id,
        _COORDINATOR,
        _WORKER,
        instructions="Fix login",
        model="openai:gpt-5.5",
        effort="high",
    )
    client = SimpleNamespace(threads=AsyncMock())
    client.threads.get.return_value = {"status": "busy" if busy else "idle"}
    client.threads.get_state.return_value = {"values": {"messages": []}}
    dispatched: list[dict[str, object]] = []
    fail_first_wake = not busy

    async def dispatch(thread_id: str, assistant_id: str, **kwargs: object) -> dict[str, str]:
        nonlocal fail_first_wake
        assert thread_id == _COORDINATOR
        assert kwargs["multitask_strategy"] == "enqueue"
        if fail_first_wake:
            fail_first_wake = False
            raise ConnectionError("temporary dispatch failure")
        dispatched.append(kwargs)
        client.threads.get.return_value = {"status": "busy"}
        return {"run_id": "notification"}

    monkeypatch.setattr(event_matches, "dispatch_client", lambda: client)
    monkeypatch.setattr(event_matches, "create_durable_run", dispatch)
    monkeypatch.setattr(service, "recipient_config", AsyncMock(return_value={}))
    monkeypatch.setattr(events.EventSubscription, "deliver_to", AsyncMock())
    payload = {"values": {"messages": [{"type": "ai", "content": "Login passes"}]}}
    if not busy:
        with pytest.raises(ConnectionError, match="temporary dispatch failure"):
            await events.worker_finished(_WORKER, "run", "success", payload)
        assert len(await EventMatch.owed(_COORDINATOR, [])) == 1
    assert await events.worker_finished(_WORKER, "run", "success", payload)
    (original,) = await EventMatch.owed(_COORDINATOR, [])
    assert "Login passes" in original.content
    assert len(dispatched) == (0 if busy else 1)

    async with postgres.session() as session:
        await session.execute(
            update(EventMatch)
            .where(EventMatch.id == original.id)
            .values(matched_at=datetime.now(UTC) - timedelta(days=3))
        )
    assert await events.worker_finished(_WORKER, "run", "success", payload)
    (retried,) = await EventMatch.owed(_COORDINATOR, [])
    assert retried.id == original.id
    assert len(dispatched) == (0 if busy else 1)

    if busy:
        client.threads.get.return_value = {"status": "idle"}
        assert await EventMatch.deliver(_COORDINATOR, "enqueue")
    assert len(dispatched) == 1
    messages = EventMatch.messages([retried])
    assert await EventMatch.owed(_COORDINATOR, messages) == []
    client.threads.get_state.return_value = {"values": {"messages": messages}}
    client.threads.get.return_value = {"status": "idle"}
    assert await events.worker_finished(_WORKER, "run", "success", payload)
    assert len(dispatched) == 1
    context = await store.load_context(_COORDINATOR)
    assert context is not None and context.task.status == "active"
