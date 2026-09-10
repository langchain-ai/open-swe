"""Bounded indexed analytics queries for dashboard metrics."""

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text

from agent.analytics.database import connection
from agent.analytics.emitter import opaque_person, workspace_id
from agent.analytics.summaries import summary_metadata
from agent.config import ENV


def period_start(period: str | None) -> datetime:
    days = 7 if period == "7d" else 30
    if period == "all":
        raw = ENV.ANALYTICS_EPOCH.require()
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    return datetime.now(UTC) - timedelta(days=days)


async def usage_leaderboard(
    *, period: str | None, limit: int, current_login: str | None, admin: bool
) -> dict[str, Any]:
    start = period_start(period)
    max_rows = min(max(limit, 1), 100)
    current_person = opaque_person("github", current_login)
    sql = text(
        """
        WITH run_stats AS (
            SELECT user_id, count(*) AS agent_runs,
                   COALESCE(sum(total_tokens), 0) AS total_tokens,
                   COALESCE(sum(EXTRACT(EPOCH FROM (terminal_at - started_at)))
                       FILTER (WHERE terminal_at >= started_at), 0) AS duration_seconds,
                   count(*) FILTER (WHERE terminal_at >= started_at) AS finished_runs,
                   count(*) FILTER (WHERE technical_status = 'completed') AS completed_runs,
                   count(*) FILTER (WHERE technical_status = 'failed') AS failed_runs,
                   count(*) FILTER (WHERE technical_status = 'canceled') AS canceled_runs
            FROM run_projection
            WHERE workspace_id = :workspace_id AND started_at >= :start AND user_id IS NOT NULL
            GROUP BY user_id
        ), pr_stats AS (
            SELECT r.user_id, count(DISTINCT p.pr_id) AS prs_opened,
                   count(DISTINCT p.pr_id) FILTER (WHERE p.current_state = 'merged') AS merged_prs
            FROM pr_projection p JOIN run_projection r
              ON r.workspace_id = p.workspace_id AND r.run_id = p.opening_run_id
            WHERE p.workspace_id = :workspace_id AND p.opened_at >= :start
            GROUP BY r.user_id
        ), cost_stats AS (
            SELECT r.user_id, COALESCE(sum(c.cost_usd), 0) AS total_cost_usd,
                   count(*) FILTER (WHERE c.status = 'complete') AS known_cost_runs,
                   count(*) AS cost_run_count
            FROM run_projection r LEFT JOIN latest_cost_projection c
              ON c.workspace_id = r.workspace_id AND c.run_id = r.run_id
            WHERE r.workspace_id = :workspace_id AND r.started_at >= :start AND r.user_id IS NOT NULL
            GROUP BY r.user_id
        ), combined AS (
            SELECT r.*, COALESCE(p.prs_opened, 0) AS prs_opened,
                   COALESCE(p.merged_prs, 0) AS merged_prs,
                   COALESCE(c.total_cost_usd, 0) AS total_cost_usd,
                   COALESCE(c.known_cost_runs, 0) AS known_cost_runs,
                   COALESCE(c.cost_run_count, 0) AS cost_run_count,
                   row_number() OVER (ORDER BY COALESCE(p.merged_prs, 0) DESC,
                       COALESCE(p.prs_opened, 0) DESC, r.agent_runs DESC, r.user_id) AS rank
            FROM run_stats r LEFT JOIN pr_stats p USING (user_id)
            LEFT JOIN cost_stats c USING (user_id)
        )
        SELECT combined.*, d.github_login, d.display_name, d.email
        FROM combined LEFT JOIN identity_directory d
          ON d.workspace_id = :workspace_id AND d.person_id = combined.user_id
        WHERE rank <= :limit OR user_id = :current_person
        ORDER BY rank LIMIT :bounded_limit
        """
    )
    async with connection() as conn:
        result = await conn.execute(
            sql,
            {
                "workspace_id": workspace_id(),
                "start": start,
                "limit": max_rows,
                "bounded_limit": max_rows + 1,
                "current_person": current_person,
            },
        )
        rows = []
        for row in result.mappings():
            is_current = row["user_id"] == current_person
            named = admin or is_current
            finished = int(row["finished_runs"] or 0)
            rows.append(
                {
                    "rank": int(row["rank"]),
                    "user": {
                        "name": (row["display_name"] or row["github_login"] or "Open SWE user")
                        if named
                        else "Open SWE user",
                        "github_login": row["github_login"] if named else None,
                        "email": row["email"] if is_current else None,
                    },
                    "is_current": is_current,
                    "favorite_model": "unavailable",
                    "agent_runs": int(row["agent_runs"]),
                    "completed_runs": int(row["completed_runs"]),
                    "failed_runs": int(row["failed_runs"]),
                    "canceled_runs": int(row["canceled_runs"]),
                    "prs_opened": int(row["prs_opened"]),
                    "merged_prs": int(row["merged_prs"]),
                    "agent_loc": 0,
                    "additions": 0,
                    "deletions": 0,
                    "total_tokens": int(row["total_tokens"]),
                    "total_cost_usd": float(row["total_cost_usd"]),
                    "cost_completeness": "complete"
                    if row["known_cost_runs"] == row["cost_run_count"]
                    else "partial"
                    if row["known_cost_runs"]
                    else "unavailable",
                    "avg_run_seconds": float(row["duration_seconds"]) / finished
                    if finished
                    else 0.0,
                }
            )
        total_members = await conn.scalar(
            text(
                "SELECT count(DISTINCT user_id) FROM run_projection WHERE workspace_id = "
                ":workspace_id AND started_at >= :start AND user_id IS NOT NULL"
            ),
            {"workspace_id": workspace_id(), "start": start},
        )
        watermark = await conn.scalar(
            text("SELECT max(recorded_at) FROM events WHERE workspace_id = :workspace_id"),
            {"workspace_id": workspace_id()},
        )
    current_user_rank = None
    for row in rows:
        if row.pop("is_current"):
            current_user_rank = row["rank"]
    return {
        "period": period if period in {"7d", "30d", "all"} else "30d",
        "rows": rows,
        "total_members": int(total_members or 0),
        "current_user_rank": current_user_rank,
        "generated_at_ms": int(datetime.now(UTC).timestamp() * 1000),
        "reviewer_stats": await reviewer_stats(start),
        **summary_metadata(watermark=watermark, completeness="epoch_forward_only"),
    }


async def reviewer_stats(start: datetime) -> dict[str, Any]:
    async with connection() as conn:
        result = await conn.execute(
            text(
                """
                SELECT (SELECT count(DISTINCT pr_id) FROM review_projection
                        WHERE workspace_id = :workspace_id AND published_at >= :start) AS reviewed_prs,
                       count(DISTINCT pr_id) AS prs_with_findings,
                       count(*) AS surfaced_findings,
                       count(*) FILTER (WHERE current_state = 'resolved') AS resolved,
                       count(*) FILTER (WHERE current_state = 'dismissed') AS dismissed,
                       count(*) FILTER (WHERE current_state = 'open') AS open,
                       COALESCE(sum(reopened_count), 0) AS reopened
                FROM finding_projection WHERE workspace_id = :workspace_id AND surfaced_at >= :start
                """
            ),
            {"workspace_id": workspace_id(), "start": start},
        )
        row = result.mappings().one()
    surfaced = int(row["surfaced_findings"] or 0)
    resolved = int(row["resolved"] or 0)
    return {
        "period": "custom",
        "reviewed_prs": int(row["reviewed_prs"] or 0),
        "prs_with_findings": int(row["prs_with_findings"] or 0),
        "findings_recorded": surfaced,
        "surfaced_findings": surfaced,
        "addressed_findings": resolved,
        "resolved_after_update": resolved,
        "dismissed_findings": int(row["dismissed"] or 0),
        "unresolved_surfaced_findings": int(row["open"] or 0),
        "reopened_findings": int(row["reopened"] or 0),
        "resolution_rate": resolved / surfaced if surfaced else 0.0,
        "human_replies": 0,
        "severity_counts": {},
        "top_categories": [],
        "generated_at_ms": int(datetime.now(UTC).timestamp() * 1000),
    }


async def pr_merge_rate_by_model(
    *, period: str | None, maturity_days: int | None = None, admin: bool = False
) -> dict[str, Any]:
    start = period_start(period)
    days = maturity_days or ENV.ANALYTICS_PR_MATURITY_DAYS.get_int(14)
    days = min(max(days, 1), 365)
    minimum = 1 if admin else ENV.ANALYTICS_MIN_COHORT_SIZE.get_int(5)
    async with connection() as conn:
        result = await conn.execute(
            text(
                """
                SELECT p.originating_model_id, p.model_attribution_quality, m.provider_model_id,
                    count(*) FILTER (WHERE current_state = 'merged') AS merged,
                    count(*) FILTER (WHERE current_state = 'closed_without_merge') AS closed,
                    count(*) FILTER (WHERE current_state = 'open' AND opened_at <= :mature_before) AS mature_pending,
                    count(*) FILTER (WHERE current_state = 'open' AND opened_at > :mature_before) AS waiting,
                    count(*) AS cohort_size
                FROM pr_projection p LEFT JOIN model_directory m
                  ON m.workspace_id = p.workspace_id AND m.model_id = p.originating_model_id
                WHERE p.workspace_id = :workspace_id AND opened_at >= :start AND opened_at <= :as_of
                GROUP BY p.originating_model_id, p.model_attribution_quality, m.provider_model_id
                HAVING count(*) >= :minimum
                ORDER BY count(*) DESC, originating_model_id
                LIMIT 100
                """
            ),
            {
                "workspace_id": workspace_id(),
                "start": start,
                "as_of": datetime.now(UTC),
                "mature_before": datetime.now(UTC) - timedelta(days=days),
                "minimum": minimum,
            },
        )
        cohorts = []
        for row in result.mappings():
            merged = int(row["merged"] or 0)
            closed = int(row["closed"] or 0)
            pending = int(row["mature_pending"] or 0)
            decided = merged + closed
            mature = decided + pending
            cohorts.append(
                {
                    "model_id": row["provider_model_id"],
                    "model_attribution_quality": row["model_attribution_quality"],
                    "merged": merged,
                    "closed_without_merge": closed,
                    "mature_pending": pending,
                    "waiting": int(row["waiting"] or 0),
                    "cohort_size": int(row["cohort_size"]),
                    "decided_denominator": decided,
                    "decided_merge_rate": merged / decided if decided else None,
                    "mature_denominator": mature,
                    "mature_cohort_merge_share": merged / mature if mature else None,
                }
            )
        watermark = await conn.scalar(
            text("SELECT max(recorded_at) FROM events WHERE workspace_id = :workspace_id"),
            {"workspace_id": workspace_id()},
        )
    return {
        "metric": "pr_merge_rate_by_originating_model",
        "definition": "PR-open-date cohorts attributed to the opening run's effective primary model.",
        "maturity_days": days,
        "period": period if period in {"7d", "30d", "all"} else "30d",
        "suppression_threshold": minimum,
        "cohorts": cohorts,
        **summary_metadata(watermark=watermark, completeness="epoch_forward_only"),
    }
