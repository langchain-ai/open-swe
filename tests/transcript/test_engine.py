"""The guarantees the transcript engine makes to every writer.

These run against a real migrated schema: the version assignment, the receipt
dedupe and the projections are all SQL, so an in-memory double would not be
testing the thing that has to hold.
"""

from datetime import UTC, datetime
from uuid import uuid7

import pytest
from sqlalchemy import text

from agent.database import postgres
from agent.transcript.engine import Command, ThreadNotTranscribed, append, has_transcript
from agent.transcript.events import (
    MessageAppended,
    MessageCompleted,
    MessageSender,
    ThreadCreated,
    TurnRequested,
)
from agent.transcript.snapshot import load_snapshot


def _created(title: str = "A thread") -> ThreadCreated:
    return ThreadCreated(
        title=title,
        source="dashboard",
        owner_login="test-user",
        metadata={"owner_login": "test-user", "visibility": "public"},
    )


async def _create(thread_id: str) -> None:
    await append(
        thread_id,
        [
            Command(
                command_id=f"thread:{thread_id}:created",
                event=_created(),
                actor_kind="user",
            )
        ],
    )


async def test_versions_are_gapless_across_appends(registry_db: None) -> None:
    thread_id = str(uuid7())
    turn_id = uuid7()
    await _create(thread_id)
    result = await append(
        thread_id,
        [
            Command(
                command_id=f"turn:{turn_id}:requested",
                event=TurnRequested(
                    turn_id=turn_id,
                    message_id="human-1",
                    text="do the thing",
                    sender=MessageSender(login="test-user", kind="dashboard"),
                ),
                actor_kind="user",
                turn_id=turn_id,
            ),
            Command(
                command_id="message:ai-1:appended",
                event=MessageAppended(turn_id=turn_id, message_id="ai-1", text="on it"),
                actor_kind="agent",
                turn_id=turn_id,
            ),
        ],
    )

    assert result.versions == [2, 3]
    assert [event.version for event in result.events] == [2, 3]
    assert await has_transcript(thread_id)
    async with postgres.read_only_transaction() as conn:
        versions = (
            (
                await conn.execute(
                    text("SELECT version FROM thread_event WHERE thread_id = :t ORDER BY version"),
                    {"t": thread_id},
                )
            )
            .scalars()
            .all()
        )
    assert list(versions) == [1, 2, 3]

    snapshot = await load_snapshot(thread_id)
    assert snapshot is not None
    assert snapshot.version == 3
    assert [turn.state for turn in snapshot.turns] == ["requested"]


async def test_a_replayed_command_reports_its_stored_version(registry_db: None) -> None:
    thread_id = str(uuid7())
    turn_id = uuid7()
    await _create(thread_id)
    command = Command(
        command_id=f"turn:{turn_id}:requested",
        event=TurnRequested(
            turn_id=turn_id,
            message_id="human-1",
            text="do the thing",
            sender=MessageSender(login="test-user", kind="dashboard"),
        ),
        actor_kind="user",
        turn_id=turn_id,
    )

    first = await append(thread_id, [command])
    replayed = await append(thread_id, [command])

    assert first.versions == [2]
    assert replayed.versions == [2]
    assert replayed.events == []
    async with postgres.read_only_transaction() as conn:
        events = (
            await conn.execute(
                text("SELECT count(*) FROM thread_event WHERE thread_id = :t"),
                {"t": thread_id},
            )
        ).scalar_one()
    assert events == 2


async def test_fragments_concatenate_until_the_canonical_text_replaces_them(
    registry_db: None,
) -> None:
    thread_id = str(uuid7())
    turn_id = uuid7()
    await _create(thread_id)
    await append(
        thread_id,
        [
            Command(
                command_id="flush-1",
                event=MessageAppended(turn_id=turn_id, message_id="ai-1", text="Hello"),
                actor_kind="agent",
                turn_id=turn_id,
            ),
            Command(
                command_id="flush-2",
                event=MessageAppended(
                    turn_id=turn_id, message_id="ai-1", text=" world", reasoning="thinking"
                ),
                actor_kind="agent",
                turn_id=turn_id,
            ),
        ],
    )

    streamed = await load_snapshot(thread_id)
    assert streamed is not None
    assert [(m.text, m.reasoning, m.streaming) for m in streamed.messages] == [
        ("Hello world", "thinking", True)
    ]

    await append(
        thread_id,
        [
            Command(
                command_id="message:ai-1:completed",
                event=MessageCompleted(
                    turn_id=turn_id,
                    message_id="ai-1",
                    role="ai",
                    text="Hello world!",
                    reasoning="thought about it",
                    created_at=datetime.now(UTC),
                ),
                actor_kind="agent",
                turn_id=turn_id,
            )
        ],
    )

    completed = await load_snapshot(thread_id)
    assert completed is not None
    assert [(m.text, m.reasoning, m.streaming) for m in completed.messages] == [
        ("Hello world!", "thought about it", False)
    ]


async def test_an_untranscribed_thread_rejects_everything_but_creation(
    registry_db: None,
) -> None:
    thread_id = str(uuid7())
    turn_id = uuid7()

    assert not await has_transcript(thread_id)
    with pytest.raises(ThreadNotTranscribed):
        await append(
            thread_id,
            [
                Command(
                    command_id="flush-1",
                    event=MessageAppended(turn_id=turn_id, message_id="ai-1", text="Hello"),
                    actor_kind="agent",
                    turn_id=turn_id,
                )
            ],
        )
