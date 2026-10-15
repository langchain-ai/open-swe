"""Closing a turn from outside the graph: the completion webhook and cancels.

The middleware normally ends a turn itself. These helpers are the safety net for
the cases where it cannot — the process died, or the user cancelled the run —
and they only ever act on a turn that is still open. The command ids match the
ones the middleware uses, so whichever writer gets there first wins and the
other is deduplicated by its receipt.
"""

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from sqlalchemy import text

from openswe.database import postgres
from openswe.transcript.engine import Command, append
from openswe.transcript.events import TurnCompleted, TurnFailed, TurnInterrupted

logger = logging.getLogger(__name__)

type TurnOutcome = Literal["completed", "failed", "interrupted"]
_OPEN_STATES = frozenset({"requested", "running"})


async def settle_run_turn(
    thread_id: str,
    run_id: str | None,
    *,
    outcome: TurnOutcome,
    error: str | None = None,
) -> UUID | None:
    """End the open turn of ``run_id`` (or the thread's newest open turn).

    Returns the turn it settled, or ``None`` when the thread has no transcript
    or its turn was already closed by the middleware.
    """
    if not postgres.configured():
        return None
    turn_id = await _open_turn(thread_id, run_id)
    if turn_id is None:
        return None
    event: TurnCompleted | TurnFailed | TurnInterrupted
    if outcome == "completed":
        event = TurnCompleted(turn_id=turn_id, run_id=run_id)
    elif outcome == "failed":
        event = TurnFailed(turn_id=turn_id, run_id=run_id, error=error or "run failed")
    else:
        event = TurnInterrupted(turn_id=turn_id, run_id=run_id)
    await append(
        thread_id,
        [
            Command(
                command_id=f"turn:{turn_id}:{outcome}",
                event=event,
                actor_kind="user" if outcome == "interrupted" else "system",
                run_id=run_id,
                turn_id=turn_id,
            ),
        ],
    )
    logger.info(
        "Settled a transcript turn from outside the graph",
        extra={
            "transcript": {
                "thread_id": thread_id,
                "run_id": run_id,
                "turn_id": str(turn_id),
                "outcome": outcome,
            }
        },
    )
    return turn_id


async def fail_unstarted_turn(thread_id: str, turn_id: UUID, *, error: str) -> None:
    """Close a turn no run will ever start; left open, it reads as queued forever."""
    await append(
        thread_id,
        [
            Command(
                command_id=f"turn:{turn_id}:failed",
                event=TurnFailed(turn_id=turn_id, error=error),
                actor_kind="system",
                turn_id=turn_id,
            )
        ],
    )


@dataclass(frozen=True)
class OpenTurn:
    turn_id: UUID
    run_id: str | None


async def steer_target(thread_id: str) -> OpenTurn | None:
    """The turn a follow-up joins: the running one, else the newest still waiting to start.

    Only turns requested after the thread's newest closed one count: an older
    open turn was stranded by a start that never ran or a settlement that failed.
    Modeled in ``docs/tla/SteerRace.tla`` as ``JoinTarget``.
    """
    if not postgres.configured():
        return None
    async with postgres.read_only_transaction() as conn:
        result = await conn.execute(
            text(
                """
                SELECT turn_id, run_id FROM thread_turn
                WHERE thread_id = :thread_id
                  AND state IN ('requested', 'running')
                  AND requested_at > COALESCE(
                      (
                          SELECT max(requested_at) FROM thread_turn
                          WHERE thread_id = :thread_id
                            AND state NOT IN ('requested', 'running')
                      ),
                      '-infinity'
                  )
                ORDER BY state = 'running' DESC, requested_at DESC, turn_id DESC
                LIMIT 1
                """
            ),
            {"thread_id": thread_id},
        )
        row = result.mappings().one_or_none()
    return None if row is None else OpenTurn(turn_id=row["turn_id"], run_id=row["run_id"])


async def recorded_turn_id(thread_id: str, command_id: str) -> UUID | None:
    """The turn of the event ``command_id`` already appended, if it did.

    A command that was deduplicated by its receipt leaves the caller holding a
    turn id nothing was written under; this recovers the one that was.
    """
    if not postgres.configured():
        return None
    async with postgres.read_only_transaction() as conn:
        result = await conn.execute(
            text(
                """
                SELECT turn_id FROM thread_event
                WHERE thread_id = :thread_id AND command_id = :command_id
                LIMIT 1
                """
            ),
            {"thread_id": thread_id, "command_id": command_id},
        )
        return result.scalar_one_or_none()


@dataclass(frozen=True)
class RunTurn:
    turn_id: UUID
    open: bool


async def run_turn(thread_id: str, run_id: str) -> RunTurn | None:
    """The newest turn ``run_id`` reported starting, or ``None`` when it reported none."""
    async with postgres.read_only_transaction() as conn:
        result = await conn.execute(
            text(
                """
                SELECT turn_id, state FROM thread_turn
                WHERE thread_id = :thread_id AND run_id = :run_id
                ORDER BY requested_at DESC, turn_id DESC
                LIMIT 1
                """
            ),
            {"thread_id": thread_id, "run_id": run_id},
        )
        row = result.mappings().one_or_none()
    return None if row is None else RunTurn(row["turn_id"], row["state"] in _OPEN_STATES)


async def superseded_turns(
    thread_id: str, run_id: str, turn_id: UUID
) -> list[tuple[UUID, str | None]]:
    """Turns other runs left running, with their runs; a thread runs one run at a time."""
    async with postgres.read_only_transaction() as conn:
        result = await conn.execute(
            text(
                """
                SELECT turn_id, run_id FROM thread_turn
                WHERE thread_id = :thread_id AND state = 'running'
                  AND turn_id <> :turn_id AND run_id IS DISTINCT FROM :run_id
                """
            ),
            {"thread_id": thread_id, "run_id": run_id, "turn_id": turn_id},
        )
        return [(row.turn_id, row.run_id) for row in result]


async def recorded_message_ids(thread_id: str, message_ids: Sequence[str]) -> set[str]:
    """Which of ``message_ids`` the thread's transcript already holds."""
    if not message_ids:
        return set()
    async with postgres.read_only_transaction() as conn:
        result = await conn.execute(
            text(
                """
                SELECT DISTINCT payload->>'message_id' FROM thread_event
                WHERE thread_id = :thread_id
                  AND event_type IN ('turn.requested', 'message.completed')
                  AND payload->>'message_id' = ANY(:message_ids)
                """
            ),
            {"thread_id": thread_id, "message_ids": list(message_ids)},
        )
        return {value for value in result.scalars() if isinstance(value, str)}


async def _open_turn(thread_id: str, run_id: str | None) -> UUID | None:
    """The turn this run is still executing.

    With a ``run_id``, the turn that reported it wins, and once that turn is
    closed there is nothing to settle: a run must never close the next turn
    that is still waiting to start. Only a run that never reported
    ``turn.started`` falls back to the newest open turn without a run, and a
    settlement with no ``run_id`` (a whole-thread cancel) takes the newest open
    turn regardless.
    """
    if run_id is not None:
        owned = await run_turn(thread_id, run_id)
        if owned is not None:
            return owned.turn_id if owned.open else None
    async with postgres.read_only_transaction() as conn:
        result = await conn.execute(
            text(
                """
                SELECT turn_id FROM thread_turn
                WHERE thread_id = :thread_id
                  AND state IN ('requested', 'running')
                  AND (CAST(:run_id AS text) IS NULL OR run_id IS NULL)
                ORDER BY requested_at DESC, turn_id DESC
                LIMIT 1
                """
            ),
            {"thread_id": thread_id, "run_id": run_id},
        )
        return result.scalar_one_or_none()
