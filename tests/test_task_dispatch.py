import asyncio
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager

import pytest
from langchain.agents import create_agent
from langchain.agents.middleware.types import AgentState
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import AIMessage
from langgraph.runtime import Runtime
from sqlalchemy import TextClause

from agent.database import postgres
from agent.middleware.task_dispatch import TaskDispatchMiddleware
from agent.middleware.trace import OpenSWEMiddleware
from agent.tasks import store


class _Receipts:
    def __init__(self) -> None:
        self.authorized = {("worker", "dispatch-1")}
        self.receipts: dict[tuple[str, str], str] = {}
        self.lock = asyncio.Lock()

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[_Receipts]:
        async with self.lock:
            yield self

    async def scalar(self, statement: TextClause, params: Mapping[str, str]) -> str | None:
        identity = (params["thread_id"], params["dispatch_key"])
        if identity not in self.authorized:
            return None
        return self.receipts.setdefault(identity, params["invocation_id"])


@pytest.fixture
def receipts(monkeypatch: pytest.MonkeyPatch) -> _Receipts:
    receipts = _Receipts()
    monkeypatch.setattr(postgres, "require_configured", lambda: None)
    monkeypatch.setattr(postgres, "transaction", receipts.transaction)
    return receipts


class _Preparation(OpenSWEMiddleware[AgentState]):
    def __init__(self) -> None:
        self.prepared = 0

    async def abefore_agent(self, state: AgentState, runtime: Runtime) -> dict[str, object] | None:
        self.prepared += 1
        return None


async def test_duplicate_dispatch_stops_before_preparation_and_model_but_same_invocation_resumes(
    receipts: _Receipts,
) -> None:
    preparation = _Preparation()
    graph = create_agent(
        FakeListChatModel(responses=["first execution", "resumed execution", "user follow-up"]),
        middleware=[TaskDispatchMiddleware("worker"), preparation],
    )
    config = {
        "configurable": {"thread_id": "worker", "invocation_id": "invocation-1"},
        "metadata": {"task_dispatch_key": "dispatch-1"},
    }
    first = await graph.ainvoke({"messages": [("human", "Work")]}, config)
    assert first["messages"][-1].content == "first execution"
    duplicate = await graph.ainvoke(
        {"messages": [("human", "Work")]},
        {**config, "configurable": {"thread_id": "worker", "invocation_id": "invocation-2"}},
    )
    assert not any(isinstance(message, AIMessage) for message in duplicate["messages"])
    assert preparation.prepared == 1
    resumed = await graph.ainvoke({"messages": [("human", "Resume")]}, config)
    assert resumed["messages"][-1].content == "resumed execution"
    ordinary = await graph.ainvoke(
        {"messages": [("human", "Follow up")]},
        {"configurable": {"thread_id": "worker", "invocation_id": "invocation-3"}},
    )
    assert ordinary["messages"][-1].content == "user follow-up"
    assert preparation.prepared == 3
    assert receipts.receipts == {("worker", "dispatch-1"): "invocation-1"}


@pytest.mark.parametrize(
    "thread_id,dispatch_key,invocation_id,error",
    [
        ("worker", "forged-dispatch", "invocation-1", PermissionError),
        ("other-thread", "dispatch-1", "invocation-1", PermissionError),
        ("worker", "dispatch-1", "", ValueError),
    ],
)
async def test_receipts_require_persisted_matching_work_and_invocation_identity(
    receipts: _Receipts,
    thread_id: str,
    dispatch_key: str,
    invocation_id: str,
    error: type[Exception],
) -> None:
    with pytest.raises(error):
        await store.claim_task_dispatch(thread_id, dispatch_key, invocation_id)
    assert receipts.receipts == {}


async def test_dispatch_without_invocation_identity_fails_before_model(receipts: _Receipts) -> None:
    graph = create_agent(
        FakeListChatModel(responses=["must not execute"]),
        middleware=[TaskDispatchMiddleware("worker")],
    )
    with pytest.raises(ValueError, match="invocation identity"):
        await graph.ainvoke(
            {"messages": [("human", "Work")]},
            {"metadata": {"task_dispatch_key": "dispatch-1"}},
        )
    assert receipts.receipts == {}


async def test_postgres_claims_one_invocation_for_worker_and_coordinator_event(
    registry_db: None,
) -> None:
    await store.ensure_task(
        "coordinator", workspace="default", title="Fix login", acceptance_criteria=["Login works"]
    )
    await store.create_delegation(
        "coordinator", worker_thread_id="worker", instructions="Fix login", model=None, effort=None
    )
    await store.record_worker_dispatch("worker", dispatch_key="dispatch-1", content="Fix login")
    claims = await asyncio.gather(
        store.claim_task_dispatch("worker", "dispatch-1", "invocation-1"),
        store.claim_task_dispatch("worker", "dispatch-1", "invocation-2"),
    )
    assert sorted(claims) == [False, True]
    winner = "invocation-1" if claims[0] else "invocation-2"
    assert await store.task_dispatch_invocation("worker", "dispatch-1") == winner
    assert await store.claim_task_dispatch("worker", "dispatch-1", winner)
    for thread_id, key in [("coordinator", "dispatch-1"), ("worker", "forged-dispatch")]:
        with pytest.raises(PermissionError):
            await store.claim_task_dispatch(thread_id, key, "forged-invocation")
    event = await store.record_event(
        "worker", event_key="progress-1", kind="progress", content="Regression reproduced"
    )
    key = f"task-event:{event.id}"
    assert await store.claim_task_dispatch("coordinator", key, "event-invocation")
    assert not await store.claim_task_dispatch("coordinator", key, "duplicate-event-invocation")
    with pytest.raises(PermissionError):
        await store.claim_task_dispatch("worker", key, "forged-invocation")
