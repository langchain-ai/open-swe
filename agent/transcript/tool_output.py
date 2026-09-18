"""Tool output: written beside an event, read back by the transcript API.

A ``tool.completed`` event carries a preview only, and the full text lives in
``thread_tool_output``. The write happens in the same transaction as the append
that references it, exactly like an attachment, so the blob is a peer of the log
rather than a projection of it — rebuilding the projections never touches it.
"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from agent.database import postgres
from agent.transcript.events import TOOL_OUTPUT_PREVIEW_CHARS, ToolCompleted

MAX_TOOL_OUTPUT_CHARS = 256 * 1024
"""The cap on what is stored; the endpoint has no more than this either."""


def normalize(event: ToolCompleted, output: str | None) -> ToolCompleted:
    """The event as the log should record it, given the output actually stored.

    The writer describes the output it produced; the cap applied here is what
    decides whether all of it is kept. Settling ``output_truncated``,
    ``has_output`` and a missing preview against the stored text before the
    event is written keeps a replay and a snapshot telling the same story.
    """
    if output is None:
        return event
    stored = output[:MAX_TOOL_OUTPUT_CHARS]
    return event.model_copy(
        update={
            "output_truncated": event.output_truncated or len(stored) < len(output),
            "has_output": True,
            "output_preview": event.output_preview
            if event.output_preview is not None
            else stored[:TOOL_OUTPUT_PREVIEW_CHARS] or None,
        }
    )


async def write(
    conn: AsyncConnection, thread_id: str, tool_call_id: str, output: str | None
) -> None:
    """Store ``output`` for one tool call in the caller's transaction."""
    if output is None:
        return
    await conn.execute(
        text(
            """
            INSERT INTO thread_tool_output (thread_id, tool_call_id, output)
            VALUES (:thread_id, :tool_call_id, :output)
            ON CONFLICT (thread_id, tool_call_id) DO UPDATE SET output = EXCLUDED.output
            """
        ),
        {
            "thread_id": thread_id,
            "tool_call_id": tool_call_id,
            "output": output[:MAX_TOOL_OUTPUT_CHARS],
        },
    )


async def load(thread_id: str, tool_call_id: str) -> str | None:
    """The stored output of one tool call, or ``None`` when none was stored."""
    async with postgres.snapshot_transaction() as conn:
        row = (
            await conn.execute(
                text(
                    """
                    SELECT output FROM thread_tool_output
                    WHERE thread_id = :thread_id AND tool_call_id = :tool_call_id
                    """
                ),
                {"thread_id": thread_id, "tool_call_id": tool_call_id},
            )
        ).scalar_one_or_none()
    return row
