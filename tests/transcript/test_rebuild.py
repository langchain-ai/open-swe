"""That the projections really are disposable: a thread folded again from its log.

Against a real schema, because what is being tested is that replaying the log
through the same SQL projections reproduces the read model byte for byte, and
that the blobs beside the log survive a rebuild that never touches them.
"""

from datetime import UTC, datetime
from uuid import UUID, uuid7

import pytest
from sqlalchemy import text

from agent.database import postgres
from agent.transcript import tool_output
from agent.transcript.engine import Command, ThreadNotTranscribed, append
from agent.transcript.events import (
    MessageAppended,
    MessageCompleted,
    MessageSender,
    ThreadCreated,
    ThreadMetaPatch,
    ThreadMetaUpdated,
    ToolCompleted,
    ToolStarted,
    TurnCheckpointCompleted,
    TurnCompleted,
    TurnRequested,
)
from agent.transcript.rebuild import rebuild_thread_projections
from agent.transcript.snapshot import TranscriptSnapshot, load_snapshot

OWNER = "test-user"
TOOL_OUTPUT = "line of output\n" * 500
"""Multi-KB, so the preview and the blob are visibly different things."""


async def _seed(thread_id: str, turn_id: UUID) -> None:
    await append(
        thread_id,
        [
            Command(
                command_id=f"thread:{thread_id}:created",
                event=ThreadCreated(
                    title="A thread",
                    source="dashboard",
                    owner_login=OWNER,
                    metadata={"owner_login": OWNER, "visibility": "public"},
                ),
                actor_kind="user",
            )
        ],
    )
    await append(
        thread_id,
        [
            Command(
                command_id=f"turn:{turn_id}:requested",
                event=TurnRequested(
                    turn_id=turn_id,
                    message_id="human-1",
                    text="do the thing",
                    sender=MessageSender(login=OWNER, kind="dashboard"),
                ),
                actor_kind="user",
                turn_id=turn_id,
            ),
            Command(
                command_id=f"message:{turn_id}:1",
                event=MessageAppended(turn_id=turn_id, message_id="ai-1", text="Work"),
                actor_kind="agent",
                turn_id=turn_id,
            ),
            Command(
                command_id=f"message:{turn_id}:2",
                event=MessageAppended(turn_id=turn_id, message_id="ai-1", text="ing on it"),
                actor_kind="agent",
                turn_id=turn_id,
            ),
            Command(
                command_id=f"message:{turn_id}:done",
                event=MessageCompleted(
                    turn_id=turn_id,
                    message_id="ai-1",
                    role="ai",
                    text="Working on it",
                    created_at=datetime(2026, 9, 18, 12, 0, tzinfo=UTC),
                ),
                actor_kind="agent",
                turn_id=turn_id,
            ),
            Command(
                command_id=f"tool:{turn_id}:started",
                event=ToolStarted(
                    turn_id=turn_id,
                    tool_call_id="call-1",
                    message_id="ai-1",
                    name="read_file",
                    input={"path": "README.md"},
                ),
                actor_kind="agent",
                turn_id=turn_id,
            ),
            Command(
                command_id=f"tool:{turn_id}:completed",
                event=ToolCompleted(
                    turn_id=turn_id,
                    tool_call_id="call-1",
                    status="completed",
                    output_preview=TOOL_OUTPUT[:2000],
                    has_output=True,
                ),
                tool_output=TOOL_OUTPUT,
                actor_kind="agent",
                turn_id=turn_id,
            ),
            Command(
                command_id=f"turn:{turn_id}:checkpoint",
                event=TurnCheckpointCompleted(
                    turn_id=turn_id,
                    checkpoint_turn_count=1,
                    checkpoint_ref=f"refs/open-swe/checkpoints/{thread_id}/turn/{turn_id}",
                    commit="0" * 40,
                    status="ready",
                    assistant_message_id="ai-1",
                ),
                actor_kind="agent",
                turn_id=turn_id,
            ),
            Command(
                command_id=f"turn:{turn_id}:completed",
                event=TurnCompleted(turn_id=turn_id),
                actor_kind="agent",
                turn_id=turn_id,
            ),
            Command(
                command_id=f"thread:{thread_id}:renamed",
                event=ThreadMetaUpdated(
                    patch=ThreadMetaPatch(title="Renamed", metadata={"visibility": "private"})
                ),
                actor_kind="system",
            ),
        ],
    )


def _comparable(snapshot: TranscriptSnapshot) -> dict[str, object]:
    """The snapshot without the wall clock a projection stamps as it writes."""
    return snapshot.model_dump(mode="json", exclude={"thread": {"updated_at"}})


async def test_rebuilding_a_thread_folds_its_projections_back_out_of_the_log(
    registry_db: None,
) -> None:
    thread_id = str(uuid7())
    turn_id = uuid7()
    await _seed(thread_id, turn_id)

    before = await load_snapshot(thread_id)
    assert before is not None
    assert before.tool_calls[0].has_output
    assert await tool_output.load(thread_id, "call-1") == TOOL_OUTPUT

    async with postgres.transaction() as conn:
        await conn.execute(
            text("DELETE FROM thread_message WHERE thread_id = :thread_id"),
            {"thread_id": thread_id},
        )
        await conn.execute(
            text("UPDATE thread SET status = 'error', title = 'wrong' WHERE thread_id = :t"),
            {"t": thread_id},
        )

    replayed = await rebuild_thread_projections(thread_id)
    assert replayed == before.version

    after = await load_snapshot(thread_id)
    assert after is not None
    assert _comparable(after) == _comparable(before)
    assert after.version == before.version
    assert after.tool_calls[0].has_output
    assert await tool_output.load(thread_id, "call-1") == TOOL_OUTPUT


async def test_rebuilding_a_thread_with_no_transcript_raises(registry_db: None) -> None:
    with pytest.raises(ThreadNotTranscribed):
        await rebuild_thread_projections(str(uuid7()))
