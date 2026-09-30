"""Paging a long thread backwards: disjoint pages, every turn, no cross-thread reach.

Against the real schema, because the keyset bound, the ordering and the page
boundary are all SQL — a double would agree with itself and prove nothing.
"""

from datetime import UTC, datetime
from uuid import uuid7

import pytest
from fastapi import HTTPException

from agent.transcript.cursor import TurnPageCursor, decode_turn_cursor, encode_turn_cursor
from agent.transcript.engine import Command, append
from agent.transcript.events import (
    CheckpointFile,
    MessageCompleted,
    MessageSender,
    ThreadCreated,
    ToolCompleted,
    ToolStarted,
    TurnCheckpointCompleted,
    TurnRequested,
)
from agent.transcript.routes import api_get_thread_transcript_turns
from agent.transcript.snapshot import load_snapshot, load_turn_page

OWNER = "test-user"
SESSION = {"sub": OWNER, "email": f"{OWNER}@example.com"}


async def _create(thread_id: str) -> None:
    await append(
        thread_id,
        [
            Command(
                command_id=f"thread:{thread_id}:created",
                event=ThreadCreated(
                    title="A long thread",
                    source="dashboard",
                    owner_login=OWNER,
                    visibility="public",
                    metadata={
                        "owner_login": OWNER,
                        "visibility": "public",
                        "source": "dashboard",
                    },
                ),
                actor_kind="user",
            )
        ],
    )


async def _turns(thread_id: str, count: int) -> list[str]:
    """``count`` settled turns, each with a message, a reply, a tool call and a checkpoint."""
    turn_ids: list[str] = []
    for index in range(count):
        turn_id = uuid7()
        turn_ids.append(str(turn_id))
        await append(
            thread_id,
            [
                Command(
                    command_id=f"turn:{turn_id}:requested",
                    event=TurnRequested(
                        turn_id=turn_id,
                        message_id=f"human-{index}",
                        text=f"question {index}",
                        sender=MessageSender(login=OWNER, kind="dashboard"),
                    ),
                    actor_kind="user",
                    turn_id=turn_id,
                ),
                Command(
                    command_id=f"tool:{turn_id}:started",
                    event=ToolStarted(
                        turn_id=turn_id,
                        tool_call_id=f"call-{index}",
                        name="read_file",
                        input={},
                    ),
                    actor_kind="agent",
                    turn_id=turn_id,
                ),
                Command(
                    command_id=f"tool:{turn_id}:completed",
                    event=ToolCompleted(
                        turn_id=turn_id,
                        tool_call_id=f"call-{index}",
                        status="completed",
                        output_preview=f"output {index}",
                        has_output=True,
                    ),
                    tool_output=f"output {index}",
                    actor_kind="agent",
                    turn_id=turn_id,
                ),
                Command(
                    command_id=f"turn:{turn_id}:checkpoint",
                    event=TurnCheckpointCompleted(
                        turn_id=turn_id,
                        checkpoint_turn_count=index + 1,
                        checkpoint_ref=f"refs/open-swe/checkpoints/{thread_id}/turn/{index + 1}",
                        commit=f"{index:040x}",
                        status="ready",
                        files=[
                            CheckpointFile(
                                path=f"file-{index}.py",
                                additions=index,
                                deletions=0,
                                status="modified",
                            )
                        ],
                        assistant_message_id=f"ai-{index}",
                    ),
                    actor_kind="agent",
                    turn_id=turn_id,
                ),
                Command(
                    command_id=f"message:{turn_id}:completed",
                    event=MessageCompleted(
                        turn_id=turn_id,
                        message_id=f"ai-{index}",
                        role="ai",
                        text=f"answer {index}",
                        created_at=datetime.now(UTC),
                    ),
                    actor_kind="agent",
                    turn_id=turn_id,
                ),
            ],
        )
    return turn_ids


async def test_paging_back_covers_every_turn_exactly_once(registry_db: None) -> None:
    thread_id = str(uuid7())
    await _create(thread_id)
    expected = await _turns(thread_id, 11)

    snapshot = await load_snapshot(thread_id, limit=4)
    assert snapshot is not None
    seen = [str(turn.turn_id) for turn in snapshot.turns]
    assert seen == expected[-4:]
    assert snapshot.older_cursor is not None
    # The window's messages and tool calls are the window's own, not the thread's.
    assert [message.text for message in snapshot.messages if message.role == "human"] == [
        f"question {index}" for index in range(7, 11)
    ]
    assert [call.tool_call_id for call in snapshot.tool_calls] == [
        f"call-{index}" for index in range(7, 11)
    ]

    cursor = snapshot.older_cursor
    pages = 0
    while cursor is not None:
        page = await load_turn_page(thread_id, before=_decoded(cursor, thread_id), limit=4)
        pages += 1
        assert page.turns, "a cursor was handed out for an empty page"
        page_ids = [str(turn.turn_id) for turn in page.turns]
        assert not set(page_ids) & set(seen)
        seen = page_ids + seen
        assert {str(message.turn_id) for message in page.messages} == set(page_ids)
        assert {str(call.turn_id) for call in page.tool_calls} == set(page_ids)
        cursor = page.older_cursor
    assert pages == 2
    assert seen == expected


def _decoded(cursor: str, thread_id: str) -> TurnPageCursor:
    decoded = decode_turn_cursor(cursor)
    assert decoded is not None and decoded.thread_id == thread_id
    return decoded


async def test_every_page_carries_each_turns_checkpoint(registry_db: None) -> None:
    """The checkpoint is what a later reader diffs turns with, so it pages with them."""
    thread_id = str(uuid7())
    await _create(thread_id)
    await _turns(thread_id, 5)

    snapshot = await load_snapshot(thread_id, limit=2)
    assert snapshot is not None
    newest = snapshot.turns[-1].checkpoint
    assert newest is not None
    assert newest.checkpoint_turn_count == 5
    assert newest.checkpoint_ref == f"refs/open-swe/checkpoints/{thread_id}/turn/5"
    assert newest.commit == f"{4:040x}"
    assert newest.status == "ready"
    assert [(file.path, file.additions) for file in newest.files] == [("file-4.py", 4)]
    assert newest.assistant_message_id == "ai-4"

    assert snapshot.older_cursor is not None
    page = await load_turn_page(
        thread_id, before=_decoded(snapshot.older_cursor, thread_id), limit=2
    )
    assert [turn.checkpoint.checkpoint_turn_count for turn in page.turns if turn.checkpoint] == [
        2,
        3,
    ]


async def test_a_foreign_or_malformed_cursor_is_rejected(registry_db: None) -> None:
    """A cursor minted elsewhere must not silently serve this thread's newest page."""
    thread_id, other = str(uuid7()), str(uuid7())
    await _create(thread_id)
    await _create(other)
    await _turns(thread_id, 2)
    snapshot = await load_snapshot(thread_id, limit=1)
    assert snapshot is not None

    # A cursor whose boundary is real, but whose thread is not this one.
    foreign = encode_turn_cursor(
        TurnPageCursor(
            thread_id=other,
            before_requested_at=snapshot.turns[0].requested_at,
            before_turn_id=snapshot.turns[0].turn_id,
        )
    )
    for bad in (foreign, "not-a-cursor"):
        with pytest.raises(HTTPException) as raised:
            await api_get_thread_transcript_turns(thread_id, before=bad, session=SESSION)
        assert raised.value.status_code == 400
