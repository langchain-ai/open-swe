"""Durable at-least-once analytics delivery."""

import asyncio
import logging
import random
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import text

from agent.analytics.database import configured, transaction
from agent.analytics.events import EventEnvelope
from agent.analytics.ingestion import ingest
from agent.config import ENV

logger = logging.getLogger(__name__)

_STOP = asyncio.Event()
_WORKER: asyncio.Task[None] | None = None


async def enqueue(event: EventEnvelope) -> bool:
    async with transaction() as conn:
        result = await conn.execute(
            text(
                "INSERT INTO outbox (event_id, workspace_id, event_body) VALUES "
                "(:event_id, :workspace_id, CAST(:event_body AS jsonb)) ON CONFLICT DO NOTHING "
                "RETURNING event_id"
            ),
            {
                "event_id": event.event_id,
                "workspace_id": event.workspace_id,
                "event_body": event.model_dump_json(),
            },
        )
        return result.scalar_one_or_none() is not None


async def _claim(limit: int = 50) -> list[dict[str, Any]]:
    async with transaction() as conn:
        result = await conn.execute(
            text(
                """
                WITH candidates AS (
                    SELECT event_id FROM outbox
                    WHERE state IN ('pending', 'delivering')
                      AND next_attempt_at <= clock_timestamp()
                      AND (state = 'pending' OR locked_at < clock_timestamp() - interval '5 minutes')
                    ORDER BY created_at
                    FOR UPDATE SKIP LOCKED
                    LIMIT :limit
                )
                UPDATE outbox AS target
                SET state = 'delivering', locked_at = clock_timestamp(),
                    attempts = attempts + 1, updated_at = clock_timestamp()
                FROM candidates
                WHERE target.event_id = candidates.event_id
                RETURNING target.event_id, target.event_body, target.attempts
                """
            ),
            {"limit": min(max(limit, 1), 100)},
        )
        return [dict(row) for row in result.mappings()]


def _delay(attempt: int) -> float:
    base = min(3600.0, 2.0 ** min(attempt, 12))
    return base * random.uniform(0.5, 1.5)


async def _ack(event_id: UUID) -> None:
    async with transaction() as conn:
        await conn.execute(
            text(
                "UPDATE outbox SET state = 'acknowledged', acknowledged_at = clock_timestamp(), "
                "locked_at = NULL, last_error = NULL, updated_at = clock_timestamp() "
                "WHERE event_id = :event_id"
            ),
            {"event_id": event_id},
        )


async def _fail(event_id: UUID, attempt: int, exc: Exception) -> None:
    exhausted = attempt >= ENV.ANALYTICS_OUTBOX_MAX_ATTEMPTS.get_int(10)
    message = type(exc).__name__
    async with transaction() as conn:
        await conn.execute(
            text(
                "UPDATE outbox SET state = :state, dead_lettered_at = CASE WHEN :exhausted "
                "THEN clock_timestamp() ELSE NULL END, next_attempt_at = :next_attempt_at, "
                "locked_at = NULL, last_error = :error, updated_at = clock_timestamp() "
                "WHERE event_id = :event_id"
            ),
            {
                "state": "dead_letter" if exhausted else "pending",
                "exhausted": exhausted,
                "next_attempt_at": datetime.now(UTC) + timedelta(seconds=_delay(attempt)),
                "error": message,
                "event_id": event_id,
            },
        )


async def deliver_batch() -> int:
    delivered = 0
    for record in await _claim():
        event_id = UUID(str(record["event_id"]))
        try:
            event = EventEnvelope.model_validate(record["event_body"])
            await ingest(event)
            await _ack(event_id)
            delivered += 1
        except Exception as exc:  # noqa: BLE001
            await _fail(event_id, int(record["attempts"]), exc)
            logger.warning(
                "Analytics delivery failed",
                extra={
                    "analytics_event_id": str(event_id),
                    "analytics_attempt": record["attempts"],
                },
                exc_info=True,
            )
    return delivered


async def outbox_status(stale_minutes: int = 15) -> dict[str, int]:
    async with transaction() as conn:
        result = await conn.execute(
            text(
                "SELECT count(*) FILTER (WHERE state = 'pending') AS pending, count(*) FILTER "
                "(WHERE state = 'dead_letter') AS dead_letters, count(*) FILTER (WHERE state IN "
                "('pending', 'delivering') AND created_at < clock_timestamp() - "
                "(:stale_minutes * interval '1 minute')) AS stale FROM outbox"
            ),
            {"stale_minutes": min(max(stale_minutes, 1), 1440)},
        )
        row = result.mappings().one()
        return {key: int(row[key] or 0) for key in ("pending", "dead_letters", "stale")}


async def run_worker() -> None:
    while not _STOP.is_set():
        try:
            delivered = await deliver_batch()
            from agent.analytics.summaries import recompute_dirty_partitions

            await recompute_dirty_partitions(limit=20)
            await enforce_retention()
        except Exception:  # noqa: BLE001
            delivered = 0
            logger.warning("Analytics worker iteration failed", exc_info=True)
        try:
            await asyncio.wait_for(_STOP.wait(), timeout=0.25 if delivered else 5.0)
        except TimeoutError:
            pass


def start_worker() -> None:
    global _WORKER
    if not configured() or (_WORKER is not None and not _WORKER.done()):
        return
    _STOP.clear()
    _WORKER = asyncio.create_task(run_worker(), name="analytics-outbox-worker")


async def stop_worker() -> None:
    global _WORKER
    _STOP.set()
    if _WORKER is not None:
        await _WORKER
    _WORKER = None


async def enforce_retention() -> None:
    async with transaction() as conn:
        await conn.execute(
            text(
                "DELETE FROM events WHERE occurred_at < clock_timestamp() - "
                "(:months * interval '1 month')"
            ),
            {"months": ENV.ANALYTICS_RAW_EVENT_MONTHS.get_int(25)},
        )
        await conn.execute(
            text("DELETE FROM ingestion_receipts WHERE expires_at < clock_timestamp()")
        )
        await conn.execute(
            text(
                "DELETE FROM outbox WHERE state = 'acknowledged' AND acknowledged_at < "
                "clock_timestamp() - (:days * interval '1 day')"
            ),
            {"days": ENV.ANALYTICS_ACK_OUTBOX_DAYS.get_int(30)},
        )
        await conn.execute(
            text(
                "UPDATE identity_directory SET github_login = NULL, display_name = NULL, email = "
                "NULL, team_id = NULL, updated_at = clock_timestamp() WHERE anonymize_after < "
                "clock_timestamp() AND (github_login IS NOT NULL OR display_name IS NOT NULL OR "
                "email IS NOT NULL OR team_id IS NOT NULL)"
            )
        )
        await conn.execute(
            text(
                "DELETE FROM daily_summaries WHERE partition_date < current_date - "
                "(:years * interval '1 year')"
            ),
            {"years": ENV.ANALYTICS_AGGREGATE_YEARS.get_int(7)},
        )
