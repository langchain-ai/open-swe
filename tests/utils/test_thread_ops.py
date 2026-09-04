import asyncio
from typing import Any

import pytest

from agent.utils import thread_ops


class _FakeStore:
    def __init__(self) -> None:
        self.items: dict[tuple[tuple[str, ...], str], dict[str, Any]] = {}

    async def put_item(self, namespace: tuple[str, ...], key: str, value: dict[str, Any]) -> None:
        self.items[(namespace, key)] = value


class _FakeClient:
    def __init__(self) -> None:
        self.store = _FakeStore()


@pytest.mark.asyncio
async def test_queue_message_for_thread_uses_distinct_append_only_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeClient()
    monkeypatch.setattr(thread_ops, "langgraph_client", lambda: client)

    results = await asyncio.gather(
        thread_ops.queue_message_for_thread("thread-1", "first"),
        thread_ops.queue_message_for_thread("thread-1", "second"),
    )

    assert results == [True, True]
    assert len(client.store.items) == 2
    assert {item["content"] for item in client.store.items.values()} == {"first", "second"}
    assert all(
        key.startswith(thread_ops.QUEUE_MESSAGE_KEY_PREFIX) for (_, key) in client.store.items
    )
