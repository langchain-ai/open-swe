"""Expire analytics records while retaining durable projections."""

from sqlalchemy import text

from openswe.config import ENV
from openswe.database import transaction


async def enforce_retention() -> None:
    """Delete expired rows; a replica already sweeping makes this a no-op.

    Cutoffs use ``now()``, not ``clock_timestamp()``: a volatile cutoff cannot be an
    index condition, which turns every sweep into full scans of the largest tables.
    """
    async with transaction() as conn:
        if not await conn.scalar(
            text("SELECT pg_try_advisory_xact_lock(hashtextextended('analytics:retention', 0))")
        ):
            return
        await conn.execute(
            text("DELETE FROM events WHERE occurred_at < now() - (:months * interval '1 month')"),
            {"months": ENV.ANALYTICS_RAW_EVENT_MONTHS.get_int(25)},
        )
        await conn.execute(text("DELETE FROM ingestion_receipts WHERE expires_at < now()"))
        await conn.execute(
            text(
                "DELETE FROM outbox WHERE state = 'acknowledged' AND acknowledged_at < "
                "now() - (:days * interval '1 day')"
            ),
            {"days": ENV.ANALYTICS_ACK_OUTBOX_DAYS.get_int(30)},
        )
        await conn.execute(
            text(
                "UPDATE identity_directory SET github_login = NULL, display_name = NULL, "
                "display_name_source = NULL, email = NULL, team_id = NULL, "
                "updated_at = clock_timestamp() WHERE anonymize_after < "
                "now() AND (github_login IS NOT NULL OR display_name IS NOT NULL OR "
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
