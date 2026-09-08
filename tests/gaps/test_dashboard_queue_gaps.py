import asyncio
import copy
import importlib
import os
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from agent.utils import thread_ops

queue_middleware = importlib.import_module("agent.middleware.check_message_queue")

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        os.environ.get("OPEN_SWE_RUN_GAP_TESTS") != "1",
        reason="intentionally red queue contract suite",
    ),
]


class _Client:
    def __init__(self, store: object) -> None:
        self.store = store


def _texts(item: dict[str, object] | None) -> list[str]:
    if item is None:
        return []
    value = item["value"]
    assert isinstance(value, dict)
    messages = value["messages"]
    assert isinstance(messages, list)
    return [message["content"]["text"] for message in messages]


class _LostUpdateStore:
    def __init__(self) -> None:
        self.item: dict[str, object] | None = None
        self.reads = 0
        self.both_read = asyncio.Event()
        self.second_written = asyncio.Event()

    async def get_item(self, namespace: object, key: str) -> dict[str, object] | None:
        snapshot = copy.deepcopy(self.item)
        self.reads += 1
        if self.reads == 2:
            self.both_read.set()
        await self.both_read.wait()
        return snapshot

    async def put_item(self, namespace: object, key: str, value: dict[str, object]) -> None:
        if asyncio.current_task().get_name() == "append-first":
            await self.second_written.wait()
        self.item = {"value": copy.deepcopy(value)}
        if asyncio.current_task().get_name() == "append-second":
            self.second_written.set()


async def test_simultaneous_dashboard_appends_preserve_every_message(monkeypatch) -> None:
    store = _LostUpdateStore()
    monkeypatch.setattr(thread_ops, "langgraph_client", lambda: _Client(store))

    results = await asyncio.gather(
        asyncio.create_task(
            thread_ops.queue_message_for_thread("thread-1", {"text": "first"}),
            name="append-first",
        ),
        asyncio.create_task(
            thread_ops.queue_message_for_thread("thread-1", {"text": "second"}),
            name="append-second",
        ),
    )

    assert results == [True, True]
    assert _texts(store.item) == ["first", "second"]


class _AdmissionOrderStore:
    def __init__(self) -> None:
        self.item: dict[str, object] | None = None
        self.first_admitted = asyncio.Event()
        self.second_done = asyncio.Event()

    async def get_item(self, namespace: object, key: str) -> dict[str, object] | None:
        if asyncio.current_task().get_name() == "append-first":
            self.first_admitted.set()
            await self.second_done.wait()
        else:
            await self.first_admitted.wait()
        return copy.deepcopy(self.item)

    async def put_item(self, namespace: object, key: str, value: dict[str, object]) -> None:
        self.item = {"value": copy.deepcopy(value)}
        if asyncio.current_task().get_name() == "append-second":
            self.second_done.set()


async def test_fifo_follows_admission_order_not_store_completion_order(monkeypatch) -> None:
    store = _AdmissionOrderStore()
    monkeypatch.setattr(thread_ops, "langgraph_client", lambda: _Client(store))

    await asyncio.gather(
        asyncio.create_task(
            thread_ops.queue_message_for_thread("thread-1", {"text": "first"}),
            name="append-first",
        ),
        asyncio.create_task(
            thread_ops.queue_message_for_thread("thread-1", {"text": "second"}),
            name="append-second",
        ),
    )

    assert _texts(store.item) == ["first", "second"]


class _DoubleClaimStore:
    def __init__(self) -> None:
        self.value: dict[str, object] | None = {"messages": [{"content": "only once"}]}
        self.reads = 0
        self.both_read = asyncio.Event()

    async def aget(self, namespace: tuple[str, ...], key: str) -> object | None:
        if namespace[0] == "autofix":
            return None
        snapshot = copy.deepcopy(self.value)
        self.reads += 1
        if self.reads == 2:
            self.both_read.set()
        await self.both_read.wait()
        return SimpleNamespace(value=snapshot)

    async def adelete(self, namespace: tuple[str, ...], key: str) -> None:
        self.value = None


async def test_only_one_model_call_claims_each_queue_batch(monkeypatch) -> None:
    store = _DoubleClaimStore()
    monkeypatch.setattr(
        queue_middleware,
        "get_config",
        lambda: {"configurable": {"thread_id": "thread-1"}},
    )
    monkeypatch.setattr(queue_middleware, "get_store", lambda: store)

    results = await asyncio.gather(
        queue_middleware.check_message_queue_before_model.abefore_model(
            {"messages": []}, MagicMock()
        ),
        queue_middleware.check_message_queue_before_model.abefore_model(
            {"messages": []}, MagicMock()
        ),
    )

    assert sum(result is not None for result in results) == 1
    assert store.value is None


class _AppendDuringConsumeStore:
    def __init__(self) -> None:
        self.value: dict[str, object] | None = {"messages": [{"content": "old"}]}
        self.consumer_read = asyncio.Event()
        self.appended = asyncio.Event()

    async def aget(self, namespace: tuple[str, ...], key: str) -> object | None:
        if namespace[0] == "autofix":
            return None
        snapshot = copy.deepcopy(self.value)
        self.consumer_read.set()
        await self.appended.wait()
        return SimpleNamespace(value=snapshot)

    async def adelete(self, namespace: tuple[str, ...], key: str) -> None:
        self.value = None

    async def get_item(self, namespace: object, key: str) -> dict[str, object] | None:
        await self.consumer_read.wait()
        return {"value": copy.deepcopy(self.value)}

    async def put_item(self, namespace: object, key: str, value: dict[str, object]) -> None:
        self.value = copy.deepcopy(value)
        self.appended.set()


async def test_consume_does_not_delete_a_message_appended_after_claim(monkeypatch) -> None:
    store = _AppendDuringConsumeStore()
    monkeypatch.setattr(thread_ops, "langgraph_client", lambda: _Client(store))
    monkeypatch.setattr(
        queue_middleware,
        "get_config",
        lambda: {"configurable": {"thread_id": "thread-1"}},
    )
    monkeypatch.setattr(queue_middleware, "get_store", lambda: store)

    consumed, appended = await asyncio.gather(
        queue_middleware.check_message_queue_before_model.abefore_model(
            {"messages": []}, MagicMock()
        ),
        thread_ops.queue_message_for_thread("thread-1", {"text": "new"}),
    )

    assert consumed is not None
    assert appended is True
    assert store.value == {"messages": [{"content": {"text": "new"}}]}
