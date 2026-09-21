"""What one thread's transcript may not do to another's, and what a delete ends.

These run against a real migrated schema: the primary keys, the cascade and the
authorization mirror are all SQL, so an in-memory double would not be testing
the thing that has to hold.
"""

import asyncio
from contextlib import aclosing
from uuid import uuid7

import pytest
from fastapi import HTTPException
from sqlalchemy import text

from agent.database import postgres
from agent.transcript.attachments import PendingAttachment
from agent.transcript.engine import Command, append, delete_transcript
from agent.transcript.events import (
    MessageAttachment,
    MessageSender,
    RunNotice,
    ThreadCreated,
    ToolCompleted,
    ToolStarted,
    TurnCompleted,
    TurnRequested,
    TurnStarted,
)
from agent.transcript.listener import (
    _SUBSCRIBERS,
    DELETED_VERSION,
    _resync_subscribers,
    subscribe,
)
from agent.transcript.mirror import mirror_thread_metadata
from agent.transcript.routes import (
    _readable_transcript,
    _stream,
    api_get_thread_attachment,
)
from agent.transcript.snapshot import load_access, load_snapshot
from agent.transcript.turns import settle_run_turn

OWNER = "test-user"


def _created(*, visibility: str = "public") -> ThreadCreated:
    return ThreadCreated(
        title="A thread",
        source="dashboard",
        owner_login=OWNER,
        visibility=visibility,  # type: ignore[arg-type]
        metadata={"owner_login": OWNER, "visibility": visibility, "source": "dashboard"},
    )


async def _create(thread_id: str, *, visibility: str = "public") -> None:
    await append(
        thread_id,
        [
            Command(
                command_id=f"thread:{thread_id}:created",
                event=_created(visibility=visibility),
                actor_kind="user",
            )
        ],
    )


def _requested(turn_id: object, message_id: str, text_body: str) -> TurnRequested:
    return TurnRequested(
        turn_id=turn_id,  # type: ignore[arg-type]
        message_id=message_id,
        text=text_body,
        sender=MessageSender(login=OWNER, kind="dashboard"),
    )


async def test_a_replayed_message_id_cannot_reach_another_thread(registry_db: None) -> None:
    """``run.start`` takes a client-supplied message id; it is thread-scoped."""
    victim, attacker = str(uuid7()), str(uuid7())
    await _create(victim)
    await _create(attacker)
    victim_turn, attacker_turn = uuid7(), uuid7()

    await append(
        victim,
        [
            Command(
                command_id=f"turn:{victim_turn}:requested",
                event=_requested(victim_turn, "shared-id", "the victim's own words"),
                actor_kind="user",
                turn_id=victim_turn,
            )
        ],
    )
    await append(
        attacker,
        [
            Command(
                command_id=f"turn:{attacker_turn}:requested",
                event=_requested(attacker_turn, "shared-id", "overwritten"),
                actor_kind="user",
                turn_id=attacker_turn,
            )
        ],
    )

    victim_snapshot = await load_snapshot(victim)
    attacker_snapshot = await load_snapshot(attacker)
    assert victim_snapshot is not None and attacker_snapshot is not None
    assert [message.text for message in victim_snapshot.messages] == ["the victim's own words"]
    assert [message.text for message in attacker_snapshot.messages] == ["overwritten"]


async def test_a_repeated_tool_call_id_cannot_reach_another_thread(registry_db: None) -> None:
    """Tool call ids come from the model and legitimately repeat across threads."""
    first, second = str(uuid7()), str(uuid7())
    await _create(first)
    await _create(second)

    for thread_id, output in ((first, "first output"), (second, "second output")):
        turn_id = uuid7()
        await append(
            thread_id,
            [
                Command(
                    command_id=f"tool:{thread_id}:started",
                    event=ToolStarted(
                        turn_id=turn_id, tool_call_id="call_abc", name="read_file", input={}
                    ),
                    actor_kind="agent",
                    turn_id=turn_id,
                ),
                Command(
                    command_id=f"tool:{thread_id}:completed",
                    event=ToolCompleted(
                        turn_id=turn_id,
                        tool_call_id="call_abc",
                        status="completed",
                        output_preview=output,
                        has_output=True,
                    ),
                    tool_output=output,
                    actor_kind="agent",
                    turn_id=turn_id,
                ),
            ],
        )

    first_snapshot = await load_snapshot(first)
    second_snapshot = await load_snapshot(second)
    assert first_snapshot is not None and second_snapshot is not None
    assert [call.output_preview for call in first_snapshot.tool_calls] == ["first output"]
    assert [call.output_preview for call in second_snapshot.tool_calls] == ["second output"]


async def test_deleting_a_transcript_removes_everything_it_owned(registry_db: None) -> None:
    thread_id = str(uuid7())
    turn_id = uuid7()
    await _create(thread_id)
    await append(
        thread_id,
        [
            Command(
                command_id=f"turn:{turn_id}:requested",
                event=_requested(turn_id, "human-1", "hello"),
                actor_kind="user",
                turn_id=turn_id,
                attachments=(
                    PendingAttachment(
                        attachment_id=uuid7(),
                        message_id="human-1",
                        position=0,
                        mime_type="image/png",
                        file_name="shot.png",
                        data=b"bytes",
                    ),
                ),
            )
        ],
    )

    assert await delete_transcript(thread_id)

    assert await load_snapshot(thread_id) is None
    assert await load_access(thread_id) is None
    async with postgres.read_only_transaction() as conn:
        for table in ("thread_event", "thread_turn", "thread_message", "thread_attachment"):
            remaining = (
                await conn.execute(
                    text(f"SELECT count(*) FROM {table} WHERE thread_id = :t"),  # noqa: S608
                    {"t": thread_id},
                )
            ).scalar_one()
            assert remaining == 0, table
    assert not await delete_transcript(thread_id)

    # The receipts are not cascaded, so a thread recreated under the same id
    # must not be deduplicated out of existence.
    await _create(thread_id)
    assert await load_snapshot(thread_id) is not None


async def test_a_visibility_flip_reaches_the_transcript_read_path(registry_db: None) -> None:
    """The mirror is what the transcript API authorizes against, so it must follow."""
    thread_id = str(uuid7())
    await _create(thread_id, visibility="public")
    stranger = {"sub": "someone-else", "email": "someone-else@example.com"}

    await _readable_transcript(thread_id, stranger)

    await mirror_thread_metadata(thread_id, {"visibility": "private", "title": "Renamed"})

    with pytest.raises(HTTPException) as refused:
        await _readable_transcript(thread_id, stranger)
    assert refused.value.status_code == 404
    await _readable_transcript(thread_id, {"sub": OWNER, "email": None})
    snapshot = await load_snapshot(thread_id)
    assert snapshot is not None
    assert snapshot.thread.title == "Renamed"


async def test_a_live_stream_ends_when_its_reader_loses_access(registry_db: None) -> None:
    """Authorization is granted once per request; the stream outlives it."""
    thread_id = str(uuid7())
    await _create(thread_id, visibility="public")
    stranger = {"sub": "someone-else", "email": "someone-else@example.com"}

    async with aclosing(_stream(thread_id, 0, stranger)) as stream:

        async def frame() -> str:
            async with asyncio.timeout(10):
                return await anext(stream)

        assert "event: transcript" in await frame()
        assert "event: synchronized" in await frame()

        # The flip is itself an appended event, so it wakes the live loop.
        await mirror_thread_metadata(thread_id, {"visibility": "private"})

        assert await frame() == "event: revoked\ndata: {}\n\n"
        with pytest.raises(StopAsyncIteration):
            await frame()


async def test_a_stream_that_ends_during_its_replay_unsubscribes(registry_db: None) -> None:
    """A cursor past the head ends the stream before it ever awaits a notification."""
    thread_id = str(uuid7())
    await _create(thread_id)
    owner = {"sub": OWNER, "email": None}

    async with aclosing(_stream(thread_id, 10_000, owner)) as stream:
        assert await anext(stream) == "event: deleted\ndata: {}\n\n"
        with pytest.raises(StopAsyncIteration):
            await anext(stream)
    assert thread_id not in _SUBSCRIBERS

    # Abandoned mid-replay, before the live loop, the same holds.
    stream = _stream(thread_id, 0, owner)
    assert "event: transcript" in await anext(stream)
    await stream.aclose()
    assert thread_id not in _SUBSCRIBERS


async def test_a_replay_is_refused_to_a_reader_who_lost_access(registry_db: None) -> None:
    """The request was authorized while public; the replay itself sees the flip."""
    thread_id = str(uuid7())
    await _create(thread_id, visibility="public")
    stranger = {"sub": "someone-else", "email": "someone-else@example.com"}
    await _readable_transcript(thread_id, stranger)

    await mirror_thread_metadata(thread_id, {"visibility": "private"})

    async with aclosing(_stream(thread_id, 0, stranger)) as stream:
        assert await anext(stream) == "event: revoked\ndata: {}\n\n"
        with pytest.raises(StopAsyncIteration):
            await anext(stream)


async def test_a_resync_reports_a_thread_deleted_while_the_listener_was_down(
    registry_db: None,
) -> None:
    """A delete in another process notifies nobody here; the catch-up must."""
    alive, gone = str(uuid7()), str(uuid7())
    await _create(alive)
    await _create(gone)
    assert await delete_transcript(gone)

    async with subscribe(alive) as alive_versions, subscribe(gone) as gone_versions:
        await _resync_subscribers()

        snapshot = await load_snapshot(alive)
        assert snapshot is not None
        async with asyncio.timeout(10):
            assert await anext(alive_versions) == snapshot.version
            assert await anext(gone_versions) == DELETED_VERSION


async def test_a_routing_notice_survives_the_turn_that_produced_it(registry_db: None) -> None:
    """The routed-model badge is read back after a reload; offloading is not."""
    thread_id = str(uuid7())
    turn_id = uuid7()
    await _create(thread_id)
    await append(
        thread_id,
        [
            Command(
                command_id=f"turn:{turn_id}:started",
                event=TurnStarted(turn_id=turn_id, run_id="run-1"),
                actor_kind="agent",
                turn_id=turn_id,
            ),
            Command(
                command_id="notice:routed",
                event=RunNotice(
                    turn_id=turn_id, kind="model_routed", data={"model": "claude-haiku"}
                ),
                actor_kind="agent",
                turn_id=turn_id,
            ),
            Command(
                command_id="notice:offloading",
                event=RunNotice(
                    turn_id=turn_id, kind="conversation_offloading", data={"state": "running"}
                ),
                actor_kind="agent",
                turn_id=turn_id,
            ),
        ],
    )

    running = await load_snapshot(thread_id)
    assert running is not None
    assert {notice.kind for notice in running.notices} == {
        "model_routed",
        "conversation_offloading",
    }

    await append(
        thread_id,
        [
            Command(
                command_id=f"turn:{turn_id}:completed",
                event=TurnCompleted(turn_id=turn_id, run_id="run-1"),
                actor_kind="agent",
                turn_id=turn_id,
            )
        ],
    )

    settled = await load_snapshot(thread_id)
    assert settled is not None
    assert [(notice.kind, notice.data) for notice in settled.notices] == [
        ("model_routed", {"model": "claude-haiku"})
    ]


async def test_an_attachment_is_served_only_to_a_reader_of_its_own_thread(
    registry_db: None,
) -> None:
    thread_id, other_id = str(uuid7()), str(uuid7())
    await _create(thread_id, visibility="private")
    await _create(other_id)
    turn_id = uuid7()
    attachment_id = uuid7()
    await append(
        thread_id,
        [
            Command(
                command_id=f"turn:{turn_id}:requested",
                event=TurnRequested(
                    turn_id=turn_id,
                    message_id="human-1",
                    text="look",
                    sender=MessageSender(login=OWNER, kind="dashboard"),
                    attachments=[
                        MessageAttachment(
                            mime_type="image/png",
                            file_name="../../etc/passwd",
                            attachment_id=attachment_id,
                        )
                    ],
                ),
                actor_kind="user",
                turn_id=turn_id,
                attachments=(
                    PendingAttachment(
                        attachment_id=attachment_id,
                        message_id="human-1",
                        position=0,
                        mime_type="image/png",
                        file_name="../../etc/passwd",
                        data=b"pretend-png",
                    ),
                ),
            )
        ],
    )

    owner = {"sub": OWNER, "email": None}
    response = await api_get_thread_attachment(thread_id, attachment_id, owner)
    assert response.body == b"pretend-png"
    assert response.headers["content-type"] == "image/png"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["content-disposition"] == 'inline; filename="etc_passwd"'
    assert response.headers["cache-control"].startswith("private,")

    # The same id, asked for through a thread that does not own it.
    with pytest.raises(HTTPException) as missing:
        await api_get_thread_attachment(other_id, attachment_id, owner)
    assert missing.value.status_code == 404

    with pytest.raises(HTTPException) as refused:
        await api_get_thread_attachment(thread_id, attachment_id, {"sub": "stranger"})
    assert refused.value.status_code == 404


async def _turn_state(thread_id: str, turn_id: object) -> str:
    async with postgres.read_only_transaction() as conn:
        result = await conn.execute(
            text("SELECT state FROM thread_turn WHERE thread_id = :t AND turn_id = :turn"),
            {"t": thread_id, "turn": turn_id},
        )
        return result.scalar_one()


async def test_a_finished_run_never_settles_the_next_waiting_turn(registry_db: None) -> None:
    """The completion webhook of run A arrives while turn B is requested but not started."""
    thread_id = str(uuid7())
    await _create(thread_id)
    turn_a, turn_b = uuid7(), uuid7()
    await append(
        thread_id,
        [
            Command(
                command_id=f"turn:{turn_a}:requested",
                event=_requested(turn_a, "m-a", "first"),
                actor_kind="user",
                turn_id=turn_a,
            ),
            Command(
                command_id=f"turn:{turn_a}:started:run-a",
                event=TurnStarted(turn_id=turn_a, run_id="run-a"),
                actor_kind="agent",
                run_id="run-a",
                turn_id=turn_a,
            ),
            Command(
                command_id=f"turn:{turn_a}:completed",
                event=TurnCompleted(turn_id=turn_a, run_id="run-a"),
                actor_kind="agent",
                run_id="run-a",
                turn_id=turn_a,
            ),
            Command(
                command_id=f"turn:{turn_b}:requested",
                event=_requested(turn_b, "m-b", "second"),
                actor_kind="user",
                turn_id=turn_b,
            ),
        ],
    )

    assert await settle_run_turn(thread_id, "run-a", outcome="completed") is None
    assert await _turn_state(thread_id, turn_b) == "requested"

    # A run that died before ``turn.started`` still gets its turn closed.
    assert await settle_run_turn(thread_id, "run-b", outcome="failed", error="boom") == turn_b
    assert await _turn_state(thread_id, turn_b) == "failed"

    turn_c = uuid7()
    await append(
        thread_id,
        [
            Command(
                command_id=f"turn:{turn_c}:requested",
                event=_requested(turn_c, "m-c", "third"),
                actor_kind="user",
                turn_id=turn_c,
            )
        ],
    )
    # A whole-thread cancel takes whatever is open.
    assert await settle_run_turn(thread_id, None, outcome="interrupted") == turn_c
    assert await _turn_state(thread_id, turn_c) == "interrupted"


async def test_a_settled_turn_is_not_reopened_by_a_later_settlement(registry_db: None) -> None:
    """A cancel that lands while the graph is finishing must not flip to ``completed``."""
    thread_id = str(uuid7())
    await _create(thread_id)
    turn = uuid7()
    await append(
        thread_id,
        [
            Command(
                command_id=f"turn:{turn}:requested",
                event=_requested(turn, "m-1", "go"),
                actor_kind="user",
                turn_id=turn,
            ),
            Command(
                command_id=f"turn:{turn}:started:run-1",
                event=TurnStarted(turn_id=turn, run_id="run-1"),
                actor_kind="agent",
                run_id="run-1",
                turn_id=turn,
            ),
        ],
    )
    assert await settle_run_turn(thread_id, "run-1", outcome="interrupted") == turn
    await append(
        thread_id,
        [
            Command(
                command_id=f"turn:{turn}:completed",
                event=TurnCompleted(turn_id=turn, run_id="run-1"),
                actor_kind="agent",
                run_id="run-1",
                turn_id=turn,
            )
        ],
    )
    assert await _turn_state(thread_id, turn) == "interrupted"
