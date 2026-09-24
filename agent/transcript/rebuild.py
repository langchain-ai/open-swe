"""Folding a thread's read tables back out of its log.

The log is the truth and the projections are disposable, which is only true if
they can actually be thrown away: this drops one thread's projection rows and
replays every event it ever appended through :mod:`agent.transcript.projections`
in order. The blobs beside the log — attachments and tool outputs — are peers
of the events, not projections, so they are left untouched and a rebuilt tool
call still serves its full output.

The rebuild holds the same advisory lock ``append`` takes, for the whole
transaction, so no append interleaves with the replay and a reader either sees
the projections as they were or as they were rebuilt.
"""

import logging
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from agent.database import postgres
from agent.transcript import projections
from agent.transcript.engine import ThreadNotTranscribed
from agent.transcript.events import TRANSCRIPT_EVENT_ADAPTER, ThreadCreated

logger = logging.getLogger(__name__)

_PAGE_SIZE = 500
"""Events read per round trip, so a long thread never lands in memory at once."""

_PROJECTION_TABLES = (
    "thread_turn_checkpoint",
    "thread_turn",
    "thread_message",
    "thread_tool_call",
)


async def rebuild_thread_projections(thread_id: str) -> int:
    """Rebuild one thread's projections from its log, and return the events replayed.

    ``thread.version`` is left exactly as it was: it is the head of the log,
    which a rebuild does not move. Nothing is published either — no version a
    subscriber reads by changed, so there is nothing for it to catch up on.
    """
    async with postgres.transaction() as conn:
        await conn.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:thread_id))"),
            {"thread_id": thread_id},
        )
        created = await _created_event(conn, thread_id)
        for table in _PROJECTION_TABLES:
            await conn.execute(
                text(f"DELETE FROM {table} WHERE thread_id = :thread_id"),
                {"thread_id": thread_id},
            )
        await projections.reset_thread_row(conn, thread_id, created)
        replayed = await _replay(conn, thread_id)
    logger.info(
        "Rebuilt transcript projections",
        extra={"transcript": {"thread_id": thread_id, "replayed": replayed}},
    )
    return replayed


async def _created_event(conn: AsyncConnection, thread_id: str) -> ThreadCreated:
    """The ``thread.created`` event of a thread that has a transcript."""
    row = (
        await conn.execute(
            text(
                """
                SELECT event.payload
                FROM thread
                LEFT JOIN thread_event AS event
                  ON event.thread_id = thread.thread_id AND event.event_type = 'thread.created'
                WHERE thread.thread_id = :thread_id
                ORDER BY event.version
                LIMIT 1
                """
            ),
            {"thread_id": thread_id},
        )
    ).one_or_none()
    if row is None:
        raise ThreadNotTranscribed(thread_id)
    if row.payload is None:
        raise RuntimeError(f"thread {thread_id} has no thread.created event to rebuild from")
    return ThreadCreated.model_validate(row.payload)


async def _replay(conn: AsyncConnection, thread_id: str) -> int:
    """Apply every stored event in version order, a page at a time."""
    replayed = 0
    after = 0
    while True:
        rows = (
            (
                await conn.execute(
                    text(
                        """
                        SELECT version, run_id, occurred_at, payload FROM thread_event
                        WHERE thread_id = :thread_id AND version > :after
                        ORDER BY version
                        LIMIT :limit
                        """
                    ),
                    {"thread_id": thread_id, "after": after, "limit": _PAGE_SIZE},
                )
            )
            .mappings()
            .all()
        )
        if not rows:
            return replayed
        for row in rows:
            version: int = row["version"]
            run_id: str | None = row["run_id"]
            occurred_at: datetime = row["occurred_at"]
            # A stored checkpoint already carries the ordinal its append
            # resolved under this same lock, so ``projections.resolve`` would
            # only be able to disagree with the log.
            await projections.apply(
                conn,
                thread_id=thread_id,
                version=version,
                event=TRANSCRIPT_EVENT_ADAPTER.validate_python(row["payload"]),
                run_id=run_id,
                occurred_at=occurred_at,
                live=False,
            )
            after = version
        replayed += len(rows)
