import pytest

from openswe.message_queue import QueuedMessage
from openswe.utils import thread_ops


@pytest.mark.asyncio
async def test_queue_message_for_thread_deduplicates_queue_id(monkeypatch, registry_db) -> None:
    message = {"queue_id": "8a60896d-65ca-4e40-8a2d-1fbe81777001", "text": "follow up"}

    assert await thread_ops.queue_message_for_thread("thread-1", message) is True
    assert await thread_ops.queue_message_for_thread("thread-1", message) is True

    assert [queued.content for queued in await QueuedMessage.for_thread("thread-1")] == [message]
