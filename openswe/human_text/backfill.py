"""Recording the human writing Open SWE saw before ``human_text`` existed, once.

Thread messages come from the transcript's ``thread_message`` projection, all
the way back. Slack messages come from the event log, which keeps the last two
weeks of deliveries. The job works for a bounded time, then queues itself again
under its key with a cursor, and marks itself done in ``event_claim`` at the end
so later deploys skip it.
"""

import logging
import time
from datetime import datetime, timedelta
from typing import Final

from pydantic import BaseModel, ValidationError
from sqlalchemy import text

from openswe import event_claims
from openswe.database.postgres import transaction
from openswe.human_text.sources import DeletedSlackMessage, SlackHumanText, ThreadHumanText
from openswe.jobs import queue
from openswe.jobs.queue import Job
from openswe.slack.channels import SlackChannel
from openswe.slack.payloads import SlackEventEnvelope
from openswe.transcript.events import MessageSender

logger = logging.getLogger(__name__)

BACKFILL_JOB: Final = "human_text_backfill"
_DONE_SCOPE: Final = "human_text"
_DONE_KEY: Final = "backfill"
_DONE_TTL: Final = timedelta(days=365 * 100)
_PAGE: Final = 500
_RUN_BUDGET_SECONDS: Final = 240.0


class _Cursor(BaseModel):
    """Where the backfill stopped: threads first, then Slack."""

    thread_id: str = ""
    message_id: str = ""
    threads_done: bool = False
    slack_received_at: datetime | None = None


async def schedule_backfill() -> None:
    """Queue the backfill unless it already finished; a queued one keeps its cursor."""
    if await event_claims.held(_DONE_SCOPE, _DONE_KEY):
        return
    await queue.schedule(BACKFILL_JOB, key=BACKFILL_JOB, replace=False)


async def run_backfill(job: Job) -> dict[str, int]:
    try:
        cursor = _Cursor.model_validate(job.payload)
    except ValidationError:
        logger.warning("Restarting human text backfill from an unreadable cursor")
        cursor = _Cursor()
    deadline = time.monotonic() + _RUN_BUDGET_SECONDS
    recorded = {"thread_messages": 0, "slack_messages": 0}
    while not cursor.threads_done and time.monotonic() < deadline:
        recorded["thread_messages"] += await _thread_page(cursor)
    channels: dict[str, bool] = {}
    while cursor.threads_done and time.monotonic() < deadline:
        recorded["slack_messages"] += await _slack_page(cursor, channels)
        if cursor.slack_received_at is None:
            await event_claims.claim(_DONE_SCOPE, _DONE_KEY, ttl=_DONE_TTL)
            logger.info("Human text backfill finished")
            return recorded
    await queue.schedule(BACKFILL_JOB, cursor.model_dump(mode="json"), key=BACKFILL_JOB)
    return recorded


async def _thread_page(cursor: _Cursor) -> int:
    """Record one page of human thread messages and move the cursor past it."""
    async with transaction() as conn:
        rows = (
            await conn.execute(
                text(
                    """
                    SELECT thread_id, message_id, text, sender, created_at FROM thread_message
                    WHERE role = 'human' AND namespace = '{}'
                        AND (thread_id, message_id) > (:thread_id, :message_id)
                    ORDER BY thread_id, message_id
                    LIMIT :limit
                    """
                ),
                {
                    "thread_id": cursor.thread_id,
                    "message_id": cursor.message_id,
                    "limit": _PAGE,
                },
            )
        ).all()
        recorded = 0
        for row in rows:
            message = ThreadHumanText.from_message(
                thread_id=row.thread_id,
                message_id=row.message_id,
                text=row.text,
                sender=MessageSender.model_validate(row.sender) if row.sender else None,
                sent_at=row.created_at,
            )
            if message is not None:
                await message.save(conn)
                recorded += 1
    if len(rows) < _PAGE:
        cursor.threads_done = True
    if rows:
        cursor.thread_id, cursor.message_id = rows[-1].thread_id, rows[-1].message_id
    return recorded


async def _slack_page(cursor: _Cursor, channels: dict[str, bool]) -> int:
    """Record one page of logged Slack message events; clears the cursor after the last."""
    async with transaction() as conn:
        rows = (
            await conn.execute(
                text(
                    """
                    SELECT received_at, payload FROM event_log
                    WHERE source = 'slack' AND event_type IN ('message', 'app_mention')
                        AND received_at > COALESCE(CAST(:after AS timestamptz), '-infinity')
                    ORDER BY received_at
                    LIMIT :limit
                    """
                ),
                {"after": cursor.slack_received_at, "limit": _PAGE},
            )
        ).all()
    recorded = 0
    for row in rows:
        envelope = SlackEventEnvelope.parse(row.payload)
        event = envelope.event if envelope is not None else None
        if event is None:
            continue
        if (deleted := DeletedSlackMessage.from_event(event)) is not None:
            await deleted.forget()
            continue
        message = SlackHumanText.from_event(event)
        if message is None:
            continue
        # The webhook records only channels it may operate in; so does the backfill.
        if message.channel_id not in channels:
            channel = await SlackChannel.load(message.channel_id)
            channels[message.channel_id] = channel is not None and channel.context.allows_operations
        if channels[message.channel_id]:
            await message.save()
            recorded += 1
    cursor.slack_received_at = rows[-1].received_at if len(rows) == _PAGE else None
    return recorded
