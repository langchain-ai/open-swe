"""Bounded indexed analytics queries for dashboard metrics."""

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from agent.config import ENV
from agent.database.analytics import connection, reporting_metadata, workspace_id


def period_start(period: str | None) -> datetime:
    days = 7 if period == "7d" else 30
    if period == "all":
        return datetime.min.replace(tzinfo=UTC)
    return datetime.now(UTC) - timedelta(days=days)


async def _reporting_start(conn: AsyncConnection, period: str | None) -> datetime:
    cutover = await conn.scalar(text("SELECT reporting_cutover_at FROM deployment_metadata"))
    if cutover is None:
        raise RuntimeError("analytics reporting has not been activated")
    return max(period_start(period), cutover)


async def pr_merge_rate_by_model(
    *, period: str | None, maturity_days: int | None = None, admin: bool = False
) -> dict[str, Any]:
    days = maturity_days or ENV.ANALYTICS_PR_MATURITY_DAYS.get_int(14)
    days = min(max(days, 1), 365)
    minimum = 1 if admin else ENV.ANALYTICS_MIN_COHORT_SIZE.get_int(5)
    as_of = datetime.now(UTC)
    async with connection() as conn:
        await conn.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        start = await _reporting_start(conn, period)
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
                "as_of": as_of,
                "mature_before": as_of - timedelta(days=days),
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
        metadata = await reporting_metadata(conn)
        if cohorts:
            status = "ready"
        elif metadata["collection_started_at"] is None:
            status = "not_started"
        else:
            has_prs = await conn.scalar(
                text(
                    "SELECT EXISTS (SELECT 1 FROM pr_projection WHERE workspace_id = :workspace_id "
                    "AND opened_at >= :start AND opened_at <= :as_of)"
                ),
                {"workspace_id": workspace_id(), "start": start, "as_of": as_of},
            )
            status = "suppressed" if has_prs else "no_prs"
    return {
        "status": status,
        "metric": "pr_outcomes_by_opening_invocation_configured_model",
        "definition": (
            "PR-open-date cohorts grouped by the opening invocation's configured model. "
            "Routing, provider fallback, subagents, and later invocations may use other models; "
            "this metric does not allocate independent model credit."
        ),
        "maturity_days": days,
        "period": period if period in {"7d", "30d", "all"} else "30d",
        "suppression_threshold": minimum,
        "cohorts": cohorts,
        **metadata,
        "as_of": as_of.isoformat(),
    }


_USAGE_SQL = """
WITH runs AS (
    SELECT COALESCE(a.person_id, r.user_id) AS person_id, r.configured_model_id,
        COALESCE(c.total_tokens, r.total_tokens, 0) AS total_tokens,
        c.cost_usd, c.status AS cost_status,
        CASE WHEN r.terminal_at >= r.started_at
            THEN EXTRACT(EPOCH FROM r.terminal_at - r.started_at) END AS duration
    FROM run_projection r
    LEFT JOIN identity_aliases a
      ON a.workspace_id = r.workspace_id AND a.alias_person_id = r.user_id
    LEFT JOIN latest_cost_projection c
      ON c.workspace_id = r.workspace_id AND c.run_id = r.run_id
    WHERE r.workspace_id = :workspace_id AND r.user_id IS NOT NULL
      AND r.started_at >= :start AND r.started_at <= :as_of
), run_totals AS (
    SELECT person_id, count(*) AS invocations, sum(total_tokens) AS total_tokens,
        COALESCE(sum(cost_usd) FILTER (WHERE cost_status IN ('complete', 'partial')), 0)
            AS total_cost_usd,
        count(*) FILTER (WHERE cost_usd IS NULL OR cost_status = 'unavailable')
            AS invocations_without_cost,
        count(*) FILTER (WHERE cost_status = 'partial') AS invocations_with_partial_cost,
        COALESCE(avg(duration), 0) AS avg_invocation_seconds
    FROM runs GROUP BY person_id
), models AS (
    SELECT DISTINCT ON (r.person_id) r.person_id, m.provider_model_id
    FROM runs r JOIN model_directory m
      ON m.workspace_id = :workspace_id AND m.model_id = r.configured_model_id
    GROUP BY r.person_id, m.provider_model_id
    ORDER BY r.person_id, count(*) DESC, m.provider_model_id
), prs AS (
    SELECT COALESCE(a.person_id, u.user_id, r.user_id) AS person_id,
        p.current_state, COALESCE(u.additions, 0) AS additions,
        COALESCE(u.deletions, 0) AS deletions
    FROM pr_projection p
    LEFT JOIN pr_usage_projection u
      ON u.workspace_id = p.workspace_id AND u.pr_id = p.pr_id
    LEFT JOIN run_projection r
      ON r.workspace_id = p.workspace_id AND r.run_id = p.opening_run_id
    LEFT JOIN identity_aliases a
      ON a.workspace_id = p.workspace_id
     AND a.alias_person_id = COALESCE(u.user_id, r.user_id)
    WHERE p.workspace_id = :workspace_id
      AND COALESCE(u.user_id, r.user_id) IS NOT NULL
      AND p.opened_at >= :start AND p.opened_at <= :as_of
), pr_totals AS (
    SELECT person_id, count(*) AS prs_opened,
        count(*) FILTER (WHERE current_state = 'merged') AS merged_prs,
        sum(additions) AS additions, sum(deletions) AS deletions,
        sum(additions + deletions) AS agent_loc
    FROM prs GROUP BY person_id
), members AS (
    SELECT person_id FROM run_totals UNION SELECT person_id FROM pr_totals
), metrics AS (
    SELECT p.person_id, d.github_login, d.email,
        COALESCE(NULLIF(d.github_login, ''), NULLIF(split_part(d.email, '@', 1), ''),
            'Open SWE user') AS name,
        ((:current_login <> '' AND lower(d.github_login) = :current_login)
          OR (:current_email <> '' AND lower(d.email) = :current_email)) IS TRUE AS is_current,
        COALESCE(r.invocations, 0) AS invocations,
        COALESCE(r.total_tokens, 0) AS total_tokens,
        COALESCE(r.total_cost_usd, 0) AS total_cost_usd,
        COALESCE(r.invocations_without_cost, 0) AS invocations_without_cost,
        COALESCE(r.invocations_with_partial_cost, 0) AS invocations_with_partial_cost,
        COALESCE(r.avg_invocation_seconds, 0) AS avg_invocation_seconds,
        COALESCE(m.provider_model_id, 'default') AS favorite_model,
        COALESCE(pr.prs_opened, 0) AS prs_opened,
        COALESCE(pr.merged_prs, 0) AS merged_prs,
        COALESCE(pr.additions, 0) AS additions,
        COALESCE(pr.deletions, 0) AS deletions,
        COALESCE(pr.agent_loc, 0) AS agent_loc
    FROM members p
    LEFT JOIN identity_directory d
      ON d.workspace_id = :workspace_id AND d.person_id = p.person_id
    LEFT JOIN run_totals r ON r.person_id = p.person_id
    LEFT JOIN pr_totals pr ON pr.person_id = p.person_id
    LEFT JOIN models m ON m.person_id = p.person_id
), ranked AS (
    SELECT *, row_number() OVER (
        ORDER BY merged_prs DESC, agent_loc DESC, prs_opened DESC,
            invocations DESC, name, person_id
    ) AS rank FROM metrics
), selected AS (
    SELECT rank,
        jsonb_build_object(
            'rank', rank,
            'user', jsonb_build_object(
                'name', CASE WHEN :admin OR is_current OR NULLIF(github_login, '') IS NOT NULL
                    THEN name ELSE 'Open SWE user' END,
                'github_login', CASE WHEN :admin OR is_current THEN NULLIF(github_login, '') END,
                'email', CASE WHEN is_current THEN NULLIF(email, '') END),
            'favorite_model', favorite_model,
            'avg_invocation_seconds', avg_invocation_seconds,
            'avg_run_seconds', avg_invocation_seconds,
            'agent_runs', invocations, 'invocations', invocations,
            'prs_opened', prs_opened, 'merged_prs', merged_prs,
            'agent_loc', agent_loc, 'additions', additions, 'deletions', deletions,
            'total_tokens', total_tokens, 'total_cost_usd', total_cost_usd,
            'invocations_without_cost', invocations_without_cost,
            'invocations_with_partial_cost', invocations_with_partial_cost
        ) AS row
    FROM ranked WHERE rank <= :limit OR is_current
)
SELECT (SELECT COALESCE(jsonb_agg(row ORDER BY rank), '[]'::jsonb) FROM selected) AS rows,
    count(*) AS total_members,
    min(rank) FILTER (WHERE is_current) AS current_user_rank,
    COALESCE(sum(invocations_without_cost), 0)::bigint AS invocations_without_cost,
    COALESCE(sum(invocations_with_partial_cost), 0)::bigint AS invocations_with_partial_cost
FROM ranked
"""

_REVIEWER_SQL = """
WITH reviews AS (
    SELECT pr_id, finding_count FROM review_projection
    WHERE workspace_id = :workspace_id AND published_at >= :start AND published_at <= :as_of
), recorded AS (
    SELECT severity, category FROM finding_usage_projection
    WHERE workspace_id = :workspace_id AND recorded_at >= :start AND recorded_at <= :as_of
), surfaced AS (
    SELECT current_state, first_seen_revision_id, resolved_revision_id, human_replies
    FROM finding_usage_projection
    WHERE workspace_id = :workspace_id AND surfaced_at >= :start AND surfaced_at <= :as_of
), severities AS (
    SELECT severity, count(*) AS count FROM recorded
    WHERE NULLIF(severity, '') IS NOT NULL GROUP BY severity
), categories AS (
    SELECT category, count(*) AS count FROM recorded
    WHERE NULLIF(category, '') IS NOT NULL GROUP BY category
    ORDER BY count(*) DESC, category LIMIT 5
)
SELECT (SELECT count(DISTINCT pr_id) FROM reviews) AS reviewed_prs,
    (SELECT count(DISTINCT pr_id) FROM reviews WHERE finding_count > 0) AS prs_with_findings,
    (SELECT count(*) FROM recorded) AS findings_recorded,
    count(*) AS surfaced_findings,
    count(*) FILTER (WHERE current_state = 'resolved') AS addressed_findings,
    count(*) FILTER (WHERE current_state = 'resolved' AND resolved_revision_id IS NOT NULL
        AND resolved_revision_id IS DISTINCT FROM first_seen_revision_id) AS resolved_after_update,
    count(*) FILTER (WHERE current_state = 'dismissed') AS dismissed_findings,
    count(*) FILTER (WHERE current_state = 'open') AS unresolved_surfaced_findings,
    COALESCE(sum(human_replies), 0) AS human_replies,
    (SELECT COALESCE(jsonb_object_agg(severity, count), '{}'::jsonb) FROM severities)
        AS severity_counts,
    (SELECT COALESCE(jsonb_agg(jsonb_build_object('name', category, 'count', count)
        ORDER BY count DESC, category), '[]'::jsonb) FROM categories) AS top_categories
FROM surfaced
"""


async def usage_leaderboard(
    *,
    period: str | None,
    limit: int,
    current_login: str | None,
    current_email: str | None,
    admin: bool = False,
) -> dict[str, Any]:
    """Read usage and review cohorts from one bounded PostgreSQL snapshot."""
    normalized = period if period in {"7d", "30d", "all"} else "30d"
    as_of = datetime.now(UTC)
    generated_at_ms = int(as_of.timestamp() * 1000)
    parameters = {
        "workspace_id": workspace_id(),
        "as_of": as_of,
        "limit": min(max(limit, 1), 100),
        "current_login": (current_login or "").strip().lower(),
        "current_email": (current_email or "").strip().lower(),
        "admin": admin,
    }
    async with connection() as conn:
        await conn.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        parameters["start"] = await _reporting_start(conn, normalized)
        result = await conn.execute(text(_USAGE_SQL), parameters)
        usage = dict(result.mappings().one())
        result = await conn.execute(text(_REVIEWER_SQL), parameters)
        reviewer = dict(result.mappings().one())
        metadata = await reporting_metadata(conn)
    reviewer.update(
        period=normalized,
        generated_at_ms=generated_at_ms,
        resolution_rate=(
            reviewer["addressed_findings"] / reviewer["surfaced_findings"]
            if reviewer["surfaced_findings"]
            else 0.0
        ),
    )
    return {
        "period": normalized,
        **usage,
        "generated_at_ms": generated_at_ms,
        "reviewer_stats": reviewer,
        **metadata,
        "as_of": as_of.isoformat(),
    }
