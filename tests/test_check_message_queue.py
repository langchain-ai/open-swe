from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from agent.middleware.check_message_queue import (
    DASHBOARD_HANDOFF_MARKER,
    check_message_queue_before_model,
)


class _QueuedItem:
    def __init__(self, value: dict[str, Any]) -> None:
        self.value = value


class _FakeStore:
    def __init__(self, value: dict[str, Any]) -> None:
        self.value = value
        self.deleted: list[tuple[tuple[str, ...], str]] = []

    async def aget(self, namespace: tuple[str, ...], key: str) -> _QueuedItem:
        return _QueuedItem(self.value)

    async def adelete(self, namespace: tuple[str, ...], key: str) -> None:
        self.deleted.append((namespace, key))


@pytest.mark.asyncio
async def test_check_message_queue_injects_dashboard_handoff_instruction() -> None:
    store = _FakeStore(
        {
            "messages": [
                {"content": {"text": "continue in web", "source": "dashboard"}},
            ]
        }
    )

    with (
        patch(
            "agent.middleware.check_message_queue.get_config",
            return_value={"configurable": {"thread_id": "thread-1"}},
        ),
        patch("agent.middleware.check_message_queue.get_store", return_value=store),
    ):
        result = await check_message_queue_before_model.abefore_model({}, MagicMock())

    assert result is not None
    message = result["messages"][0]
    assert message["role"] == "user"
    assert DASHBOARD_HANDOFF_MARKER in message["content"][0]["text"]
    assert message["content"][1] == {"type": "text", "text": "continue in web"}
    assert store.deleted == [(("queue", "thread-1"), "pending_messages")]
