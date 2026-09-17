from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

from agent import agent_cost


async def test_legacy_payload_imports_without_scheduling(monkeypatch):
    @asynccontextmanager
    async def transaction():
        yield object()

    monkeypatch.setattr(agent_cost.database, "transaction", transaction)
    enqueue = AsyncMock()
    monkeypatch.setattr(agent_cost, "enqueue_job", enqueue)
    client = AsyncMock()
    result = await agent_cost.run_agent_cost_refresh(
        {"thread_id": "thread", "run_id": "legacy", "attempt": 4}, client=client
    )
    assert result == {"status": "queued"}
    assert enqueue.await_args.args[1:] == ("legacy", "thread", None)
    client.runs.create.assert_not_called()


async def test_conflicting_invocation_ids_do_not_enqueue(monkeypatch):
    enqueue = AsyncMock()
    monkeypatch.setattr(agent_cost, "enqueue_job", enqueue)
    result = await agent_cost.run_agent_cost_refresh(
        {"thread_id": "thread", "invocation_id": "new", "run_id": "old"}
    )
    assert result["status"] == "unavailable"
    enqueue.assert_not_awaited()
