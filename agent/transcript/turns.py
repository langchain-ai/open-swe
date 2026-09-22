"""Closing a turn from outside the graph: the completion webhook and cancels.

The middleware normally ends a turn itself. These helpers are the safety net for
the cases where it cannot — the process died, or the user cancelled the run —
and they only ever act on a turn that is still open. The command ids match the
ones the middleware uses, so whichever writer gets there first wins and the
other is deduplicated by its receipt.
"""

import logging
from typing import Literal
from uuid import UUID

from sqlalchemy import text

from agent.database import postgres
from agent.transcript.engine import Command, append
from agent.transcript.events import TurnCompleted, TurnFailed, TurnInterrupted

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


async def _open_turn(thread_id: str, run_id: str | None) -> UUID | None:
    """The turn this run is still executing.

    With a ``run_id``, the turn that reported it wins, and once that turn is
    closed there is nothing to settle: a run must never close the next turn
    that is still waiting to start. Only a run that never reported
    ``turn.started`` falls back to the newest open turn without a run, and a
    settlement with no ``run_id`` (a whole-thread cancel) takes the newest open
    turn regardless.
    """
    async with postgres.read_only_transaction() as conn:
        if run_id is not None:
            owned = await conn.execute(
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
            row = owned.mappings().one_or_none()
            if row is not None:
                return row["turn_id"] if row["state"] in _OPEN_STATES else None
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
