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
            )
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


async def _open_turn(thread_id: str, run_id: str | None) -> UUID | None:
    """The turn this run is still executing, preferring an exact ``run_id`` match.

    A turn whose ``turn.started`` never landed has no ``run_id`` yet, so the
    newest open turn on the thread is the fallback rather than nothing at all.
    """
    async with postgres.read_only_transaction() as conn:
        result = await conn.execute(
            text(
                """
                SELECT turn_id FROM thread_turn
                WHERE thread_id = :thread_id
                  AND state IN ('requested', 'running')
                  AND (
                      CAST(:run_id AS text) IS NULL
                      OR run_id IS NULL
                      OR run_id = CAST(:run_id AS text)
                  )
                ORDER BY (run_id IS NOT NULL AND run_id = CAST(:run_id AS text)) DESC,
                         requested_at DESC, turn_id DESC
                LIMIT 1
                """
            ),
            {"thread_id": thread_id, "run_id": run_id},
        )
        return result.scalar_one_or_none()
