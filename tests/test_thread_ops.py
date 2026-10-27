import pytest

from openswe.message_queue import QueuedMessage, ThreadQueue
from openswe.ui_invalidations import Topic, outbox


@pytest.mark.asyncio
async def test_a_thread_queue_deduplicates_queue_id(registry_db) -> None:
    message = {"queue_id": "8a60896d-65ca-4e40-8a2d-1fbe81777001", "text": "follow up"}

    assert await ThreadQueue("thread-1").send(message) is True
    assert await ThreadQueue("thread-1").send(message) is True

    assert [queued.content for queued in await QueuedMessage.for_thread("thread-1")] == [message]


@pytest.mark.asyncio
async def test_queued_messages_show_who_sent_what_and_refresh_their_thread(registry_db) -> None:
    dashboard = {"id": "github:alice", "github_login": "alice", "platform": "github"}
    slack = {"id": "slack:U1", "display_name": "<@U1>", "platform": "slack"}
    incident = 'INCIDENT_CONTEXT {"evidence_id": "slack:1.0"}\nDB is down'
    await ThreadQueue("thread-1").send([{"type": "text", "text": "Use main"}])
    await ThreadQueue("thread-1").send({"text": "Ping", "sender": dashboard})
    await ThreadQueue("thread-1").send({"text": incident, "sender": slack})

    previews = [await message.preview() for message in await QueuedMessage.for_thread("thread-1")]

    assert [(preview.text, preview.sender) for preview in previews] == [
        ("Use main", None),
        ("Ping", "alice"),
        ("DB is down", None),
    ]
    topic = Topic.THREAD_QUEUES.keyed("thread-1")
    assert await outbox.invalidated_since({topic: 5}) == {topic}
