"""Arm a thread's inactivity deadline when it goes idle, and clear it when it is busy again.

Called from the append path rather than from a projection, so rebuilding a
thread's read tables never re-arms a deadline that already fired.
"""

from datetime import datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from openswe.transcript.events import (
    TranscriptEvent,
    TurnCompleted,
    TurnFailed,
    TurnInterrupted,
    TurnRequested,
    TurnStarted,
)

INACTIVE_AFTER = timedelta(hours=1)

_ARM = text(
    """
    INSERT INTO thread_inactivity (thread_id, last_activity_at, due_at)
    SELECT thread_id, :at, :due_at FROM thread
    WHERE thread_id = :thread_id AND status <> 'running'
    ON CONFLICT (thread_id) DO UPDATE SET
        last_activity_at = EXCLUDED.last_activity_at, due_at = EXCLUDED.due_at
    """
)

_DISARM = text(
    """
    DELETE FROM thread_inactivity USING thread
    WHERE thread_inactivity.thread_id = :thread_id
      AND thread.thread_id = thread_inactivity.thread_id
      AND thread.status = 'running'
    """
)


async def track(
    conn: AsyncConnection, thread_id: str, event: TranscriptEvent, occurred_at: datetime
) -> None:
    match event:
        case TurnRequested() | TurnStarted():
            await conn.execute(_DISARM, {"thread_id": thread_id})
        case TurnCompleted() | TurnFailed() | TurnInterrupted():
            await conn.execute(
                _ARM,
                {"thread_id": thread_id, "at": occurred_at, "due_at": occurred_at + INACTIVE_AFTER},
            )
        case _:
            return
