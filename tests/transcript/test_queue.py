import asyncio
from datetime import UTC, datetime
from uuid import uuid7

import pytest

from openswe.transcript.engine import Command, append
from openswe.transcript.events import (
    MessageAppended,
    MessageCompleted,
    MessageSender,
    ThreadCreated,
    ThreadMetaPatch,
    ThreadMetaUpdated,
    TurnRequested,
)
from openswe.transcript.queue import DEFAULT_EVENT_TYPES, EventQueue, QueueNotFound
from openswe.users import User


async def _create(thread_id: str) -> None:
    created = ThreadCreated(
        title="A thread",
        source="dashboard",
        owner_login="owner",
        metadata={"owner_login": "owner", "visibility": "public"},
    )
    await append(thread_id, [Command(command_id="created", event=created, actor_kind="user")])


async def test_drain_waits_for_the_next_turn_and_skips_unfollowed_events(
    registry_db: None,
) -> None:
    thread_id = str(uuid7())
    await _create(thread_id)
    follower = await User.sign_in("github", "1", login="test-user")
    queue = await EventQueue.open(follower.id)
    await queue.follow(thread_id, DEFAULT_EVENT_TYPES, follower.id)

    waiting = asyncio.create_task(
        queue.drain(follower.id, login="test-user", email=None, wait=10, limit=50)
    )
    await asyncio.sleep(0.2)
    assert not waiting.done()
    turn_id = uuid7()
    now = datetime.now(UTC)
    await append(
        thread_id,
        [
            Command(
                command_id="requested",
                event=TurnRequested(
                    turn_id=turn_id,
                    message_id="m-human",
                    text="Fix the race",
                    sender=MessageSender(login="test-user", kind="user"),
                ),
                actor_kind="user",
            ),
            Command(
                command_id="fragment",
                event=MessageAppended(turn_id=turn_id, message_id="m-ai", text="Fix"),
                actor_kind="agent",
            ),
            Command(
                command_id="subagent",
                event=MessageCompleted(
                    turn_id=turn_id,
                    message_id="m-sub",
                    namespace=["task:1"],
                    role="ai",
                    text="subagent chatter",
                    created_at=now,
                ),
                actor_kind="agent",
            ),
            Command(
                command_id="answer",
                event=MessageCompleted(
                    turn_id=turn_id, message_id="m-ai", role="ai", text="Fixed", created_at=now
                ),
                actor_kind="agent",
            ),
        ],
    )

    drained = await asyncio.wait_for(waiting, 1)
    assert [(event.type, event.data.get("text")) for event in drained.events] == [
        ("turn.requested", "Fix the race"),
        ("message.completed", "Fixed"),
    ]
    assert drained.threads[0].cursor == 5

    again = await (await EventQueue.owned(follower.id, queue.id)).drain(
        follower.id, login="test-user", email=None, wait=0, limit=50
    )
    assert again.events == []
    assert again.threads[0].cursor == 5


async def test_queue_is_its_creators_and_drops_threads_they_can_no_longer_read(
    registry_db: None,
) -> None:
    thread_id = str(uuid7())
    await _create(thread_id)
    follower = await User.sign_in("github", "1", login="test-user")
    stranger = await User.sign_in("github", "2", login="reviewer")
    queue = await EventQueue.open(follower.id)
    await queue.follow(thread_id, DEFAULT_EVENT_TYPES, follower.id)

    with pytest.raises(QueueNotFound):
        await EventQueue.owned(stranger.id, queue.id)

    patch = ThreadMetaPatch(metadata={"visibility": "private"})
    await append(
        thread_id,
        [Command(command_id="private", event=ThreadMetaUpdated(patch=patch), actor_kind="system")],
    )
    drained = await queue.drain(follower.id, login="test-user", email=None, wait=0, limit=50)

    assert drained.events == []
    assert [(thread.thread_id, thread.reason) for thread in drained.dropped] == [
        (thread_id, "revoked")
    ]
    assert (await EventQueue.owned(follower.id, queue.id)).threads == []
