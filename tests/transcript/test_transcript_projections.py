"""What the read tables say once the log has been appended to.

The projections are SQL, and the parts that matter here — the ordinal a
checkpoint is given under the thread's lock, and the thread status a turn
ending settles on — only exist against a real schema.
"""

from uuid import UUID, uuid7

from agent.transcript.engine import Command, append
from agent.transcript.events import (
    MessageSender,
    ThreadCreated,
    TurnCheckpointCompleted,
    TurnCompleted,
    TurnInterrupted,
    TurnRequested,
    TurnStarted,
)
from agent.transcript.snapshot import load_events, load_snapshot


async def _create(thread_id: str) -> None:
    await append(
        thread_id,
        [
            Command(
                command_id=f"thread:{thread_id}:created",
                event=ThreadCreated(
                    title="A thread",
                    source="dashboard",
                    owner_login="test-user",
                    metadata={"owner_login": "test-user", "visibility": "public"},
                ),
                actor_kind="user",
            )
        ],
    )


async def _request_turn(thread_id: str, turn_id: UUID) -> None:
    await append(
        thread_id,
        [
            Command(
                command_id=f"turn:{turn_id}:requested",
                event=TurnRequested(
                    turn_id=turn_id,
                    message_id=f"human-{turn_id}",
                    text="do the thing",
                    sender=MessageSender(login="test-user", kind="dashboard"),
                ),
                actor_kind="user",
                turn_id=turn_id,
            )
        ],
    )


async def _end_turn(thread_id: str, turn_id: UUID, *, interrupted: bool = False, tag: str) -> None:
    event = TurnInterrupted(turn_id=turn_id) if interrupted else TurnCompleted(turn_id=turn_id)
    await append(
        thread_id,
        [
            Command(
                command_id=f"turn:{turn_id}:{tag}",
                event=event,
                actor_kind="agent",
                turn_id=turn_id,
            )
        ],
    )


def _checkpoint(thread_id: str, turn_id: UUID) -> Command:
    """A checkpoint as a writer emits it: the ordinal is the append's to assign."""
    return Command(
        command_id=f"turn:{turn_id}:checkpoint",
        event=TurnCheckpointCompleted(
            turn_id=turn_id,
            checkpoint_ref=f"refs/open-swe/checkpoints/{thread_id}/turn/{turn_id}",
            status="ready",
            commit="0" * 40,
        ),
        actor_kind="agent",
        turn_id=turn_id,
    )


async def test_two_checkpoints_are_numbered_apart(
    registry_db: None,
) -> None:
    thread_id = str(uuid7())
    first_turn, second_turn = uuid7(), uuid7()
    await _create(thread_id)
    await _request_turn(thread_id, first_turn)
    await _request_turn(thread_id, second_turn)

    first = await append(thread_id, [_checkpoint(thread_id, first_turn)])
    second = await append(thread_id, [_checkpoint(thread_id, second_turn)])

    assert first.events[0].payload["checkpoint_turn_count"] == 1
    assert second.events[0].payload["checkpoint_turn_count"] == 2

    events = await load_events(thread_id, after=0, limit=100)
    stored = [event.payload for event in events if event.event_type == "turn.checkpoint.completed"]
    assert [payload["checkpoint_turn_count"] for payload in stored] == [1, 2]

    snapshot = await load_snapshot(thread_id)
    assert snapshot is not None
    checkpoints = {
        turn.turn_id: turn.checkpoint for turn in snapshot.turns if turn.checkpoint is not None
    }
    assert checkpoints[first_turn].checkpoint_turn_count == 1
    assert checkpoints[second_turn].checkpoint_turn_count == 2
    assert checkpoints[first_turn].checkpoint_ref != checkpoints[second_turn].checkpoint_ref


async def test_a_checkpoint_replayed_for_a_turn_keeps_its_ordinal(registry_db: None) -> None:
    thread_id = str(uuid7())
    first_turn, second_turn = uuid7(), uuid7()
    await _create(thread_id)
    await _request_turn(thread_id, first_turn)
    await _request_turn(thread_id, second_turn)
    await append(thread_id, [_checkpoint(thread_id, first_turn)])
    await append(thread_id, [_checkpoint(thread_id, second_turn)])

    repeat = _checkpoint(thread_id, first_turn)
    result = await append(
        thread_id,
        [
            Command(
                command_id=f"turn:{first_turn}:checkpoint:again",
                event=repeat.event,
                actor_kind="agent",
                turn_id=first_turn,
            )
        ],
    )

    assert result.events[0].payload["checkpoint_turn_count"] == 1


async def test_ending_one_of_two_open_turns_keeps_the_thread_running(registry_db: None) -> None:
    thread_id = str(uuid7())
    first_turn, second_turn = uuid7(), uuid7()
    await _create(thread_id)
    await _request_turn(thread_id, first_turn)
    await _request_turn(thread_id, second_turn)

    await _end_turn(thread_id, first_turn, tag="completed")
    snapshot = await load_snapshot(thread_id)
    assert snapshot is not None
    assert snapshot.thread.status == "running"

    await _end_turn(thread_id, second_turn, tag="completed")
    snapshot = await load_snapshot(thread_id)
    assert snapshot is not None
    assert snapshot.thread.status == "idle"


async def test_a_late_completion_of_an_interrupted_turn_leaves_the_thread_alone(
    registry_db: None,
) -> None:
    thread_id = str(uuid7())
    interrupted_turn, next_turn = uuid7(), uuid7()
    await _create(thread_id)
    await _request_turn(thread_id, interrupted_turn)
    await _end_turn(thread_id, interrupted_turn, interrupted=True, tag="interrupted")
    await _request_turn(thread_id, next_turn)

    await _end_turn(thread_id, interrupted_turn, tag="completed")

    snapshot = await load_snapshot(thread_id)
    assert snapshot is not None
    assert snapshot.thread.status == "running"
    states = {turn.turn_id: turn.state for turn in snapshot.turns}
    assert states[interrupted_turn] == "interrupted"


async def test_a_late_start_does_not_reopen_an_interrupted_turn(registry_db: None) -> None:
    """The run's ``turn.started`` lands after the cancel that settled its turn."""
    thread_id = str(uuid7())
    turn = uuid7()
    await _create(thread_id)
    await _request_turn(thread_id, turn)
    await _end_turn(thread_id, turn, interrupted=True, tag="interrupted")

    await append(
        thread_id,
        [
            Command(
                command_id=f"turn:{turn}:started",
                event=TurnStarted(turn_id=turn, run_id="run-1"),
                actor_kind="agent",
                run_id="run-1",
                turn_id=turn,
            )
        ],
    )

    snapshot = await load_snapshot(thread_id)
    assert snapshot is not None
    assert snapshot.thread.status == "idle"
    assert [turn_view.state for turn_view in snapshot.turns] == ["interrupted"]
