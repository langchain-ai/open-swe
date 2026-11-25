"""A thread's state read, and a subagent's, get each message's recorded time."""

from datetime import UTC, datetime
from uuid import uuid7

from pydantic import JsonValue

from openswe.database import postgres
from openswe.transcript.engine import Command, append
from openswe.transcript.events import MessageCompleted, ThreadCreated, ToolStarted
from openswe.transcript.message_times import MessageTimes, SubagentTimes

SENT = datetime(2026, 11, 25, 15, 4, 5, tzinfo=UTC)


async def _thread_with_reply(
    thread_id: str, message_id: str, namespace: list[str] | None = None
) -> None:
    turn_id = uuid7()
    await append(
        thread_id,
        [
            Command(
                command_id=f"thread:{thread_id}:created",
                event=ThreadCreated(
                    title="Timed",
                    source="dashboard",
                    owner_login="test-user",
                    visibility="public",
                    metadata={"owner_login": "test-user", "visibility": "public"},
                ),
                actor_kind="user",
            ),
            Command(
                command_id=f"message:{message_id}:completed",
                event=MessageCompleted(
                    turn_id=turn_id,
                    message_id=message_id,
                    namespace=namespace or [],
                    role="ai",
                    text="hi",
                    created_at=SENT,
                ),
                actor_kind="agent",
                turn_id=turn_id,
            ),
        ],
    )


async def test_stamps_recorded_messages_and_keeps_a_providers_text_time(
    registry_db: None,
) -> None:
    thread_id, other_thread = str(uuid7()), str(uuid7())
    await _thread_with_reply(thread_id, "ai-1")
    await _thread_with_reply(thread_id, "ai-2")
    await _thread_with_reply(other_thread, "ai-elsewhere")
    messages: list[dict[str, JsonValue]] = [
        {"id": "ai-1", "type": "ai", "response_metadata": {"created_at": 1795579000.5}},
        {"id": "ai-2", "type": "ai", "response_metadata": {"created_at": "2026-11-25T15:00:00Z"}},
        {"id": "human-unrecorded", "type": "human"},
        {"id": "ai-elsewhere", "type": "ai"},
    ]

    times = await MessageTimes.of(thread_id, [str(m["id"]) for m in messages])
    times.stamp(messages)

    assert messages == [
        {"id": "ai-1", "type": "ai", "response_metadata": {"created_at": SENT.isoformat()}},
        {"id": "ai-2", "type": "ai", "response_metadata": {"created_at": "2026-11-25T15:00:00Z"}},
        {"id": "human-unrecorded", "type": "human"},
        {"id": "ai-elsewhere", "type": "ai"},
    ]


async def test_a_subagent_gets_its_own_replies_and_its_task_calls_start(
    registry_db: None,
) -> None:
    thread_id = str(uuid7())
    await _thread_with_reply(thread_id, "root")
    await _thread_with_reply(thread_id, "sub", ["call-task"])
    await _thread_with_reply(thread_id, "nested", ["call-task", "call-inner"])
    turn_id = uuid7()
    await append(
        thread_id,
        [
            Command(
                command_id="tool:call-task:started",
                event=ToolStarted(turn_id=turn_id, tool_call_id="call-task", name="task"),
                actor_kind="agent",
                turn_id=turn_id,
            )
        ],
    )

    async with postgres.snapshot_transaction() as conn:
        times = await SubagentTimes.of(thread_id, "call-task", conn=conn)
        unknown = await SubagentTimes.of(thread_id, "call-missing", conn=conn)

    assert times.prompt is not None
    assert times.messages == {"sub": SENT}
    assert unknown == SubagentTimes(prompt=None, messages={})
