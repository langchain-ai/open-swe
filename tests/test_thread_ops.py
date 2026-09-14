from unittest.mock import AsyncMock, MagicMock

import pytest

from agent.utils import thread_ops


@pytest.mark.asyncio
async def test_queue_message_for_thread_deduplicates_queue_id(monkeypatch) -> None:
    existing = {
        "content": {
            "queue_id": "8a60896d-65ca-4e40-8a2d-1fbe81777001",
            "text": "follow up",
        }
    }
    client = MagicMock()
    client.store.get_item = AsyncMock(return_value={"value": {"messages": [existing]}})
    client.store.put_item = AsyncMock()
    monkeypatch.setattr(thread_ops, "langgraph_client", lambda: client)

    queued = await thread_ops.queue_message_for_thread(
        "thread-1",
        {
            "queue_id": "8a60896d-65ca-4e40-8a2d-1fbe81777001",
            "text": "follow up",
        },
    )

    assert queued is True
    client.store.put_item.assert_not_awaited()
