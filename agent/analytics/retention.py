"""Expire analytics records while retaining durable projections."""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from agent.config import ENV
from agent.database import transaction


async def enforce_retention() -> bool:
    """Run at most once an hour across replicas; rollback makes failures retryable."""
    async with transaction() as conn:
        claimed = await conn.scalar(
            text(
                "SELECT singleton FROM analytics_retention_schedule "
                "WHERE next_run_at <= CURRENT_TIMESTAMP FOR UPDATE SKIP LOCKED"
            )
        )
        if not claimed:
            return False
        await _expire_records(conn)
        await conn.execute(
            text(
                "UPDATE analytics_retention_schedule "
                "SET next_run_at = clock_timestamp() + interval '1 hour' WHERE singleton"
            )
        )
    return True


async def _expire_records(conn: AsyncConnection) -> None:
    await conn.execute(
        text(
            "DELETE FROM events WHERE occurred_at < CURRENT_TIMESTAMP - "
            "(:months * interval '1 month')"
        ),
        {"months": ENV.ANALYTICS_RAW_EVENT_MONTHS.get_int(25)},
    )
    await conn.execute(text("DELETE FROM ingestion_receipts WHERE expires_at < CURRENT_TIMESTAMP"))
    await conn.execute(
        text(
            "DELETE FROM outbox WHERE state = 'acknowledged' AND acknowledged_at < "
            "CURRENT_TIMESTAMP - (:days * interval '1 day')"
        ),
        {"days": ENV.ANALYTICS_ACK_OUTBOX_DAYS.get_int(30)},
    )
    await conn.execute(
        text(
            "UPDATE identity_directory SET github_login = NULL, display_name = NULL, "
            "display_name_source = NULL, email = NULL, team_id = NULL, "
            "updated_at = CURRENT_TIMESTAMP WHERE anonymize_after < "
            "CURRENT_TIMESTAMP AND (github_login IS NOT NULL OR display_name IS NOT NULL OR "
            "display_name_source IS NOT NULL OR email IS NOT NULL OR team_id IS NOT NULL)"
        )
    )
    await conn.execute(
        text(
            "DELETE FROM daily_summaries WHERE partition_date < current_date - "
            "(:years * interval '1 year')"
        ),
        {"years": ENV.ANALYTICS_AGGREGATE_YEARS.get_int(7)},
    )
    await conn.execute(
        text(
            "DELETE FROM additive_event_projection WHERE partition_date < current_date - "
            "(:years * interval '1 year')"
        ),
        {"years": ENV.ANALYTICS_AGGREGATE_YEARS.get_int(7)},
    )
