"""Correctable versioned daily summaries."""

import json
from datetime import UTC, date, datetime, time

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from agent.analytics.database import transaction
from agent.config import ENV

_LATENCY_BOUNDS_MS = [60_000, 300_000, 900_000, 3_600_000, 14_400_000, 86_400_000, 604_800_000]


async def recompute_dirty_partitions(limit: int = 20) -> int:
    async with transaction() as conn:
        claimed = await conn.execute(
            text(
                "SELECT workspace_id, summary_version, family, partition_date, dimension_key FROM "
                "dirty_summary_partitions ORDER BY dirty_since FOR UPDATE SKIP LOCKED LIMIT :limit"
            ),
            {"limit": min(max(limit, 1), 100)},
        )
        partitions = [dict(row) for row in claimed.mappings()]
        for partition in partitions:
            payload = await _compute(conn, partition)
            await conn.execute(
                text(
                    """
                    INSERT INTO daily_summaries (
                        workspace_id, summary_version, family, partition_date, dimension_key,
                        counters, sums, exact_members, histogram_bounds, histogram_counts,
                        completeness, data_watermark
                    ) VALUES (
                        :workspace_id, :summary_version, :family, :partition_date, :dimension_key,
                        CAST(:counters AS jsonb), CAST(:sums AS jsonb), :exact_members,
                        :histogram_bounds, :histogram_counts, CAST(:completeness AS jsonb),
                        :data_watermark
                    ) ON CONFLICT (workspace_id, summary_version, family, partition_date, dimension_key)
                    DO UPDATE SET counters = EXCLUDED.counters, sums = EXCLUDED.sums,
                        exact_members = EXCLUDED.exact_members,
                        histogram_bounds = EXCLUDED.histogram_bounds,
                        histogram_counts = EXCLUDED.histogram_counts,
                        completeness = EXCLUDED.completeness,
                        data_watermark = EXCLUDED.data_watermark,
                        recomputed_at = clock_timestamp()
                    """
                ),
                {**partition, **payload},
            )
            await conn.execute(
                text(
                    "DELETE FROM dirty_summary_partitions WHERE workspace_id = :workspace_id AND "
                    "summary_version = :summary_version AND family = :family AND partition_date = "
                    ":partition_date AND dimension_key = :dimension_key"
                ),
                partition,
            )
        return len(partitions)


async def _compute(conn: AsyncConnection, partition: dict[str, object]) -> dict[str, object]:
    family = partition["family"]
    workspace_id = partition["workspace_id"]
    partition_date = partition["partition_date"]
    assert isinstance(partition_date, date)
    day = datetime.combine(partition_date, time.min, tzinfo=UTC)
    counters: dict[str, int] = {}
    sums: dict[str, float] = {}
    members: list[object] = []
    histogram_counts = [0] * (len(_LATENCY_BOUNDS_MS) + 1)
    if family == "pr_open_cohort":
        result = await conn.execute(
            text(
                "SELECT current_state, count(*) AS count FROM pr_projection WHERE workspace_id = "
                ":workspace_id AND opened_at >= :day AND opened_at < :day + interval '1 day' "
                "GROUP BY current_state"
            ),
            {"workspace_id": workspace_id, "day": day},
        )
        counters = {row["current_state"]: int(row["count"]) for row in result.mappings()}
    elif family == "finding_surfaced_cohort":
        result = await conn.execute(
            text(
                "SELECT current_state, count(*) AS count FROM finding_projection WHERE workspace_id "
                "= :workspace_id AND surfaced_at >= :day AND surfaced_at < :day + interval '1 day' "
                "GROUP BY current_state"
            ),
            {"workspace_id": workspace_id, "day": day},
        )
        counters = {row["current_state"]: int(row["count"]) for row in result.mappings()}
    elif family == "cost_completeness":
        result = await conn.execute(
            text(
                "SELECT count(*) AS runs, count(cost_usd) AS known, COALESCE(sum(cost_usd), 0) AS "
                "cost FROM run_projection r LEFT JOIN latest_cost_projection c USING "
                "(workspace_id, run_id) WHERE r.workspace_id = :workspace_id AND r.started_at >= "
                ":day AND r.started_at < :day + interval '1 day'"
            ),
            {"workspace_id": workspace_id, "day": day},
        )
        row = result.mappings().one()
        counters = {"runs": int(row["runs"]), "known_cost_runs": int(row["known"])}
        sums = {"cost_usd": float(row["cost"])}
    elif family == "distinct_membership":
        result = await conn.execute(
            text(
                "SELECT DISTINCT user_id FROM run_projection WHERE workspace_id = :workspace_id AND "
                "started_at >= :day AND started_at < :day + interval '1 day' AND user_id IS NOT NULL"
            ),
            {"workspace_id": workspace_id, "day": day},
        )
        members = [row[0] for row in result]
    elif family == "latency_histogram":
        result = await conn.execute(
            text(
                "SELECT EXTRACT(EPOCH FROM (resolved_at - surfaced_at)) * 1000 AS latency FROM "
                "finding_projection WHERE workspace_id = :workspace_id AND surfaced_at >= :day "
                "AND surfaced_at < :day + interval '1 day' AND resolved_at >= surfaced_at"
            ),
            {"workspace_id": workspace_id, "day": day},
        )
        for row in result:
            latency = float(row[0])
            bucket = next(
                (index for index, bound in enumerate(_LATENCY_BOUNDS_MS) if latency <= bound),
                len(_LATENCY_BOUNDS_MS),
            )
            histogram_counts[bucket] += 1
    else:
        result = await conn.execute(
            text(
                "SELECT event_name, event_count AS count FROM additive_event_projection "
                "WHERE workspace_id = :workspace_id AND partition_date = :partition_date"
            ),
            {"workspace_id": workspace_id, "partition_date": partition_date},
        )
        counters = {row["event_name"]: int(row["count"]) for row in result.mappings()}
    watermark = await conn.scalar(
        text("SELECT max(recorded_at) FROM events WHERE workspace_id = :workspace_id"),
        {"workspace_id": workspace_id},
    )
    return {
        "counters": json.dumps(counters),
        "sums": json.dumps(sums),
        "exact_members": members,
        "histogram_bounds": _LATENCY_BOUNDS_MS,
        "histogram_counts": histogram_counts,
        "completeness": json.dumps({"status": "complete"}),
        "data_watermark": watermark,
    }


def summary_metadata(*, watermark: datetime | None, completeness: str) -> dict[str, object]:
    return {
        "analytics_epoch": ENV.ANALYTICS_EPOCH.optional(),
        "summary_version": ENV.ANALYTICS_SUMMARY_VERSION.get_int(1),
        "freshness": "live_current_utc_day_plus_completed_day_summaries",
        "completeness": completeness,
        "data_watermark": watermark.isoformat() if watermark else None,
        "as_of": datetime.now(UTC).isoformat(),
    }
