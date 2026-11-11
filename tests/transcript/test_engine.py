"""The guarantees the transcript engine makes to every writer.

These run against a real migrated schema: the version assignment, the receipt
dedupe and the projections are all SQL, so an in-memory double would not be
testing the thing that has to hold.
"""

from datetime import UTC, datetime
from uuid import uuid7

import pytest
from sqlalchemy import text

from openswe.database import postgres
from openswe.threads.read_state import THREAD_READ_STATES
from openswe.transcript import tool_output
from openswe.transcript.engine import Command, ThreadNotTranscribed, append
from openswe.transcript.events import (
    MessageAppended,
    MessageCompleted,
    MessageSender,
    ThreadCreated,
    ToolCompleted,
    ToolStarted,
    TurnRequested,
)
from openswe.transcript.snapshot import load_snapshot
from openswe.users.models import User, UserIdentity


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


async def test_a_command_repeated_within_one_batch_is_appended_once(registry_db: None) -> None:
    thread_id = str(uuid7())
    turn_id = uuid7()
    await _create(thread_id)
    command = Command(
        command_id="flush-1",
        event=MessageAppended(turn_id=turn_id, message_id="ai-1", text="Hello"),
        actor_kind="agent",
        turn_id=turn_id,
    )

    result = await append(thread_id, [command, command])

    assert result.versions == [2, 2]
    assert [event.version for event in result.events] == [2]

    snapshot = await load_snapshot(thread_id)
    assert snapshot is not None
    assert snapshot.version == 2
    assert [m.text for m in snapshot.messages] == ["Hello"]


async def test_one_command_id_in_two_threads_is_two_events(registry_db: None) -> None:
    first_thread = str(uuid7())
    second_thread = str(uuid7())
    turn_id = uuid7()
    await _create(first_thread)
    await _create(second_thread)
    command = Command(
        command_id="flush-1",
        event=MessageAppended(turn_id=turn_id, message_id="ai-1", text="Hello"),
        actor_kind="agent",
        turn_id=turn_id,
    )

    first = await append(first_thread, [command])
    # The second thread is a version ahead, so the two threads disagree about
    # the version the shared command id produced.
    await append(
        second_thread,
        [
            Command(
                command_id="flush-2",
                event=MessageAppended(turn_id=turn_id, message_id="ai-1", text=" world"),
                actor_kind="agent",
                turn_id=turn_id,
            )
        ],
    )
    second = await append(second_thread, [command])

    assert first.versions == [2]
    assert second.versions == [3]
    assert [event.version for event in second.events] == [3]

    assert (await append(first_thread, [command])).versions == [2]
    assert (await append(second_thread, [command])).versions == [3]

    for thread_id, expected in ((first_thread, 2), (second_thread, 3)):
        async with postgres.read_only_transaction() as conn:
            events = (
                await conn.execute(
                    text("SELECT count(*) FROM thread_event WHERE thread_id = :t"),
                    {"t": thread_id},
                )
            ).scalar_one()
        assert events == expected


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
    assert [(m.text, m.reasoning) for m in streamed.messages] == [("Hello world", "thinking")]

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
    assert [(m.text, m.reasoning) for m in completed.messages] == [
        ("Hello world!", "thought about it")
    ]


async def test_an_untranscribed_thread_rejects_everything_but_creation(
    registry_db: None,
) -> None:
    thread_id = str(uuid7())
    turn_id = uuid7()

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


async def test_an_output_cut_at_the_cap_is_recorded_as_truncated(registry_db: None) -> None:
    thread_id = str(uuid7())
    turn_id = uuid7()
    await _create(thread_id)
    full = "x" * (tool_output.MAX_TOOL_OUTPUT_CHARS + 10)
    result = await append(
        thread_id,
        [
            Command(
                command_id="tool:call-1:started",
                event=ToolStarted(turn_id=turn_id, tool_call_id="call-1", name="shell"),
                actor_kind="agent",
                turn_id=turn_id,
            ),
            Command(
                command_id="tool:call-1:completed",
                event=ToolCompleted(turn_id=turn_id, tool_call_id="call-1", status="completed"),
                actor_kind="agent",
                turn_id=turn_id,
                tool_output=full,
            ),
        ],
    )

    stored = result.events[-1].payload
    assert stored["output_truncated"] is True
    assert stored["has_output"] is True
    assert stored["output_preview"]
    snapshot = await load_snapshot(thread_id)
    assert snapshot is not None
    call = snapshot.tool_calls[0]
    assert call.output_truncated is True
    assert call.has_output is True
    output = await tool_output.load(thread_id, "call-1")
    assert output is not None and len(output) == tool_output.MAX_TOOL_OUTPUT_CHARS


async def test_app_unread_tracks_requester_output_and_stale_acknowledgements(
    registry_db: None,
) -> None:
    alice = User(identities=[UserIdentity(provider="github", external_id="1", login="alice")])
    bob = User(identities=[UserIdentity(provider="github", external_id="2", login="bob")])
    async with postgres.session() as session:
        session.add_all([alice, bob])
    thread_id = str(uuid7())
    await _create(thread_id)
    old_response: str | None = None
    replay: Command | None = None
    for index, (kind, login, answer, eligible) in enumerate(
        [
            ("slack", f"user:{alice.id}", "Slack answer", None),
            ("dashboard", f"user:{alice.id}", "App answer", "alice"),
            ("system", "system:background-task", "", None),
            ("system", "system:event-subscription", "Async continuation", "alice"),
            ("system", "system:schedule:nightly", "Unrelated automation", None),
            ("system", "system:monitor", "Automation continuation", None),
            ("dashboard", f"user:{alice.id}", "Another app answer", "alice"),
            ("dashboard", "bob", "Bob answer", "bob"),
            ("slack", f"user:{alice.id}", "Another Slack answer", None),
        ]
    ):
        turn_id = uuid7()
        await append(
            thread_id,
            [
                Command(
                    command_id=f"request-{index}",
                    event=TurnRequested(
                        turn_id=turn_id,
                        message_id=f"human-{index}",
                        text="Request",
                        sender=MessageSender(login=login, kind=kind),
                    ),
                    actor_kind="user",
                    turn_id=turn_id,
                )
            ],
        )
        if index == 1:
            await THREAD_READ_STATES.set(
                "alice",
                thread_id,
                viewed=True,
                run_id="running",
                viewed_at_ms=1,
                response_id=None,
                acknowledge_current=False,
            )
        if kind == "dashboard":
            for annotation in ("dashboard-handoff", "thread-queue", "reply-guard"):
                await append(
                    thread_id,
                    [
                        Command(
                            command_id=f"annotation-{index}-{annotation}",
                            event=MessageCompleted(
                                turn_id=turn_id,
                                message_id=f"annotation-{index}-{annotation}",
                                role="human",
                                text="Internal run notice",
                                sender=MessageSender(login=f"system:{annotation}", kind="system"),
                                created_at=datetime.now(UTC),
                            ),
                            actor_kind="system",
                            turn_id=turn_id,
                        )
                    ],
                )
        command = Command(
            command_id=f"answer-{index}",
            event=MessageCompleted(
                turn_id=turn_id,
                message_id=f"ai-{index}",
                role="ai",
                text=answer,
                created_at=datetime.now(UTC),
            ),
            actor_kind="agent",
            turn_id=turn_id,
        )
        await append(thread_id, [command])
        saved = await THREAD_READ_STATES.saved("alice", thread_id) or {}
        if index == 0:
            assert THREAD_READ_STATES.viewed(saved) is True
            assert THREAD_READ_STATES.response_id(saved) is None
        if eligible == "alice":
            current = THREAD_READ_STATES.response_id(saved)
            assert current is not None and current != old_response
            assert THREAD_READ_STATES.viewed(saved) is False
            await THREAD_READ_STATES.set(
                "alice",
                thread_id,
                viewed=True,
                run_id="finished",
                viewed_at_ms=2,
                response_id=old_response,
                acknowledge_current=False,
            )
            assert (
                THREAD_READ_STATES.viewed(await THREAD_READ_STATES.saved("alice", thread_id) or {})
                is False
            )
            await THREAD_READ_STATES.set(
                "alice",
                thread_id,
                viewed=True,
                run_id="finished",
                viewed_at_ms=3,
                response_id=current,
                acknowledge_current=False,
            )
            assert (
                THREAD_READ_STATES.viewed(await THREAD_READ_STATES.saved("alice", thread_id) or {})
                is True
            )
            old_response = current
            replay = command
        if eligible != "alice":
            assert THREAD_READ_STATES.response_id(saved) == old_response
            assert THREAD_READ_STATES.viewed(saved) is (index != 8)
        if eligible == "bob":
            assert (
                THREAD_READ_STATES.viewed(await THREAD_READ_STATES.saved("bob", thread_id) or {})
                is False
            )
            await THREAD_READ_STATES.set(
                "alice", thread_id, viewed=False, run_id=None, viewed_at_ms=4
            )
    assert replay is not None
    await append(thread_id, [replay])
    saved = await THREAD_READ_STATES.saved(f"user:{alice.id}", thread_id) or {}
    assert THREAD_READ_STATES.response_id(saved) == old_response
    assert THREAD_READ_STATES.viewed(saved) is False
