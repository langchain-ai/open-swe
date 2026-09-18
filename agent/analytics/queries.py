"""Bounded indexed analytics queries for dashboard metrics."""

import base64
import binascii
import json
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncConnection

from agent.config import ENV
from agent.database import connection
from agent.database.analytics import reporting_metadata, workspace_id
from agent.utils.build_info import backend_build_info

UsageSort = Literal[
    "rank",
    "user",
    "favorite_model",
    "invocations",
    "threads",
    "avg_invocations_per_thread",
    "total_tokens",
    "total_cost_usd",
    "avg_invocation_seconds",
    "avg_thread_seconds",
    "prs_opened",
    "merged_prs",
    "merged_prs_per_thread",
    "agent_loc",
]
SortDirection = Literal["asc", "desc"]


class InvalidUsageCursor(ValueError):
    """Raised when a usage leaderboard cursor cannot be decoded."""


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


def _integer(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _pr_outcome_counts(row: RowMapping) -> dict[str, int | float | None]:
    merged = _integer(row["merged"])
    closed = _integer(row["closed"])
    pending = _integer(row["mature_pending"])
    waiting = _integer(row["waiting"])
    decided = merged + closed
    mature = decided + pending
    return {
        "merged": merged,
        "closed_without_merge": closed,
        "mature_pending": pending,
        "waiting": waiting,
        "cohort_size": _integer(row["cohort_size"]),
        "decided_denominator": decided,
        "decided_merge_rate": merged / decided if decided else None,
        "mature_denominator": mature,
        "mature_cohort_merge_share": merged / mature if mature else None,
    }


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
                WITH eligible_models AS (
                    SELECT originating_model_id, model_attribution_quality
                    FROM pr_projection
                    WHERE workspace_id = :workspace_id
                      AND opened_at >= :start AND opened_at <= :as_of
                      AND originating_model_id IS NOT NULL
                      AND model_attribution_quality <> 'unavailable'
                    GROUP BY originating_model_id, model_attribution_quality
                    HAVING count(*) >= :minimum
                    ORDER BY count(*) DESC, originating_model_id
                    LIMIT 100
                )
                SELECT p.originating_model_id, p.model_attribution_quality, m.provider_model_id,
                    r.configured_effort,
                    count(*) FILTER (WHERE p.current_state = 'merged') AS merged,
                    count(*) FILTER (WHERE p.current_state = 'closed_without_merge') AS closed,
                    count(*) FILTER (WHERE p.current_state = 'open'
                        AND p.opened_at <= :mature_before) AS mature_pending,
                    count(*) FILTER (WHERE p.current_state = 'open'
                        AND p.opened_at > :mature_before) AS waiting,
                    count(*) AS cohort_size,
                    avg(EXTRACT(EPOCH FROM p.outcome_at - p.opened_at))
                        FILTER (WHERE p.current_state = 'merged' AND p.outcome_at IS NOT NULL)
                        AS avg_merge_seconds,
                    avg(EXTRACT(EPOCH FROM p.opened_at - r.started_at))
                        FILTER (WHERE r.started_at IS NOT NULL AND p.opened_at >= r.started_at)
                        AS avg_delivery_seconds,
                    count(*) FILTER (WHERE r.started_at IS NOT NULL
                        AND p.opened_at >= r.started_at) AS delivery_samples,
                    (SELECT percentile_cont(0.5) WITHIN GROUP
                        (ORDER BY distance_basis_points)
                     FROM pr_projection d
                     WHERE d.workspace_id = :workspace_id
                       AND d.originating_model_id IS NOT DISTINCT FROM p.originating_model_id
                       AND d.model_attribution_quality = p.model_attribution_quality
                       AND d.opened_at >= :start AND d.opened_at <= :as_of
                       AND d.current_state = 'merged') AS median_distance_basis_points,
                    (SELECT count(distance_basis_points)
                     FROM pr_projection d
                     WHERE d.workspace_id = :workspace_id
                       AND d.originating_model_id IS NOT DISTINCT FROM p.originating_model_id
                       AND d.model_attribution_quality = p.model_attribution_quality
                       AND d.opened_at >= :start AND d.opened_at <= :as_of
                       AND d.current_state = 'merged') AS distance_sample_size
                FROM pr_projection p
                JOIN eligible_models e
                  ON e.originating_model_id IS NOT DISTINCT FROM p.originating_model_id
                  AND e.model_attribution_quality = p.model_attribution_quality
                LEFT JOIN model_directory m
                  ON m.workspace_id = p.workspace_id AND m.model_id = p.originating_model_id
                LEFT JOIN run_projection r
                  ON r.workspace_id = p.workspace_id AND r.run_id = p.opening_run_id
                WHERE p.workspace_id = :workspace_id
                  AND p.opened_at >= :start AND p.opened_at <= :as_of
                GROUP BY p.originating_model_id, p.model_attribution_quality, m.provider_model_id,
                    r.configured_effort
                ORDER BY p.originating_model_id, p.model_attribution_quality, r.configured_effort
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
        grouped: dict[tuple[object, object], dict[str, Any]] = {}
        merge_seconds_totals: dict[tuple[object, object], float] = {}
        delivery_seconds_totals: dict[tuple[object, object], float] = {}
        delivery_sample_counts: dict[tuple[object, object], int] = {}
        for row in result.mappings():
            key = (row["originating_model_id"], row["model_attribution_quality"])
            cohort = grouped.setdefault(
                key,
                {
                    "model_id": row["provider_model_id"],
                    "model_attribution_quality": row["model_attribution_quality"],
                    "merged": 0,
                    "closed_without_merge": 0,
                    "mature_pending": 0,
                    "waiting": 0,
                    "cohort_size": 0,
                    "efforts": [],
                    "median_distance_basis_points": (
                        int(row["median_distance_basis_points"])
                        if row["median_distance_basis_points"] is not None
                        else None
                    ),
                    "distance_sample_size": int(row["distance_sample_size"] or 0),
                },
            )
            effort = _pr_outcome_counts(row)
            effort["avg_merge_seconds"] = (
                float(row["avg_merge_seconds"]) if row["avg_merge_seconds"] is not None else None
            )
            cohort["efforts"].append({"effort": row["configured_effort"], **effort})
            avg_merge_seconds = effort["avg_merge_seconds"]
            merged_count = effort["merged"]
            if isinstance(avg_merge_seconds, float) and isinstance(merged_count, int):
                merge_seconds_totals[key] = (
                    merge_seconds_totals.get(key, 0.0) + avg_merge_seconds * merged_count
                )
            if row["avg_delivery_seconds"] is not None and row["delivery_samples"]:
                delivery_seconds_totals[key] = (
                    delivery_seconds_totals.get(key, 0.0)
                    + float(row["avg_delivery_seconds"]) * row["delivery_samples"]
                )
                delivery_sample_counts[key] = (
                    delivery_sample_counts.get(key, 0) + row["delivery_samples"]
                )
            for field in (
                "merged",
                "closed_without_merge",
                "mature_pending",
                "waiting",
                "cohort_size",
            ):
                cohort[field] += effort[field]
        cohorts = []
        for key, cohort in grouped.items():
            merge_seconds_total = merge_seconds_totals.get(key, 0.0)
            cohort["avg_merge_seconds"] = (
                merge_seconds_total / cohort["merged"] if cohort["merged"] else None
            )
            delivery_samples = delivery_sample_counts.get(key, 0)
            cohort["avg_delivery_seconds"] = (
                delivery_seconds_totals.get(key, 0.0) / delivery_samples
                if delivery_samples
                else None
            )
            decided = cohort["merged"] + cohort["closed_without_merge"]
            mature = decided + cohort["mature_pending"]
            cohorts.append(
                {
                    **cohort,
                    "decided_denominator": decided,
                    "decided_merge_rate": cohort["merged"] / decided if decided else None,
                    "mature_denominator": mature,
                    "mature_cohort_merge_share": cohort["merged"] / mature if mature else None,
                }
            )
        if not admin:
            for cohort in cohorts:
                efforts = cohort["efforts"]
                if isinstance(efforts, list) and any(
                    effort["cohort_size"] < minimum for effort in efforts
                ):
                    cohort["efforts"] = []
        cohorts.sort(key=lambda cohort: (-_integer(cohort["cohort_size"]), str(cohort["model_id"])))
        unavailable_threads = []
        if admin:
            unavailable_threads = [
                str(thread_id)
                for thread_id in (
                    await conn.execute(
                        text(
                            "SELECT DISTINCT e.thread_id FROM pr_projection p "
                            "JOIN events e ON e.workspace_id = p.workspace_id "
                            "AND e.pr_id = p.pr_id AND e.event_name = 'pr.opened' "
                            "WHERE p.workspace_id = :workspace_id "
                            "AND p.opened_at >= :start AND p.opened_at <= :as_of "
                            "AND (p.originating_model_id IS NULL "
                            "OR p.model_attribution_quality = 'unavailable') "
                            "AND e.thread_id IS NOT NULL ORDER BY e.thread_id LIMIT 100"
                        ),
                        {"workspace_id": workspace_id(), "start": start, "as_of": as_of},
                    )
                ).scalars()
            ]
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
        "unavailable_thread_ids": unavailable_threads,
        **metadata,
        "as_of": as_of.isoformat(),
        "build_info": backend_build_info(),
    }


def _encode_usage_cursor(
    as_of: datetime,
    offset: int,
    workspace: UUID,
    period: str,
    sort: UsageSort,
    direction: SortDirection,
) -> str:
    payload = json.dumps(
        {
            "as_of": as_of.isoformat(),
            "offset": offset,
            "period": period,
            "workspace_id": str(workspace),
            "sort": sort,
            "direction": direction,
        },
        separators=(",", ":"),
    ).encode()
    return base64.urlsafe_b64encode(payload).decode().rstrip("=")


def _decode_usage_cursor(
    cursor: str,
    workspace: UUID,
    period: str,
    sort: UsageSort,
    direction: SortDirection,
) -> tuple[datetime, int]:
    try:
        encoded = cursor.encode("ascii")
        payload = json.loads(
            base64.b64decode(encoded + b"=" * (-len(encoded) % 4), altchars=b"-_", validate=True)
        )
        if not isinstance(payload, dict) or set(payload) != {
            "as_of",
            "offset",
            "period",
            "workspace_id",
            "sort",
            "direction",
        }:
            raise ValueError
        as_of = datetime.fromisoformat(payload["as_of"])
        offset = payload["offset"]
        if (
            payload["workspace_id"] != str(workspace)
            or payload["period"] != period
            or payload["sort"] != sort
            or payload["direction"] != direction
            or as_of.tzinfo is None
            or not isinstance(offset, int)
            or isinstance(offset, bool)
            or offset < 0
        ):
            raise ValueError
        return as_of.astimezone(UTC), offset
    except (
        binascii.Error,
        TypeError,
        UnicodeEncodeError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        ValueError,
    ):
        raise InvalidUsageCursor("invalid usage leaderboard cursor") from None


_USAGE_SQL = """
WITH runs AS (
    SELECT COALESCE(a.person_id, r.user_id) AS person_id, r.configured_model_id,
        r.configured_effort, r.thread_id,
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
), thread_durations AS (
    SELECT person_id, thread_id, sum(duration) AS duration
    FROM runs WHERE thread_id IS NOT NULL GROUP BY person_id, thread_id
), run_totals AS (
    SELECT person_id, count(*) AS invocations, count(DISTINCT thread_id) AS threads,
        sum(total_tokens) AS total_tokens,
        COALESCE(sum(cost_usd) FILTER (WHERE cost_status IN ('complete', 'partial')), 0)
            AS total_cost_usd,
        count(*) FILTER (WHERE cost_usd IS NULL OR cost_status = 'unavailable')
            AS invocations_without_cost,
        count(*) FILTER (WHERE cost_status = 'partial') AS invocations_with_partial_cost,
        COALESCE(avg(duration), 0) AS avg_invocation_seconds,
        COALESCE((SELECT avg(t.duration) FROM thread_durations t
            WHERE t.person_id = runs.person_id), 0) AS avg_thread_seconds
    FROM runs GROUP BY person_id
), models AS (
    SELECT DISTINCT ON (r.person_id) r.person_id, m.provider_model_id
    FROM runs r JOIN model_directory m
      ON m.workspace_id = :workspace_id AND m.model_id = r.configured_model_id
    GROUP BY r.person_id, m.provider_model_id
    ORDER BY r.person_id, count(*) DESC, m.provider_model_id
), efforts AS (
    SELECT DISTINCT ON (r.person_id, m.provider_model_id)
        r.person_id, m.provider_model_id, r.configured_effort
    FROM runs r JOIN model_directory m
      ON m.workspace_id = :workspace_id AND m.model_id = r.configured_model_id
    GROUP BY r.person_id, m.provider_model_id, r.configured_effort
    ORDER BY r.person_id, m.provider_model_id, count(*) DESC, r.configured_effort
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
    SELECT p.person_id, d.display_name, d.github_login, d.email,
        ((:current_login <> '' AND lower(d.github_login) = :current_login)
          OR (:current_email <> '' AND lower(d.email) = :current_email)) IS TRUE AS is_current,
        -- An email prefix never lands here: it is only the disclosed label
        -- for an identity-less row with the policy off.
        NULLIF(d.display_name, '') AS stored_name,
        CASE WHEN :anonymize_others AND NOT :admin AND NOT (((:current_login <> ''
                  AND lower(d.github_login) = :current_login)
              OR (:current_email <> '' AND lower(d.email) = :current_email)) IS TRUE)
            THEN NULL ELSE d.github_login END AS disclosed_login,
        :anonymize_others AND NOT :admin AND NOT (((:current_login <> ''
            AND lower(d.github_login) = :current_login)
        OR (:current_email <> '' AND lower(d.email) = :current_email)) IS TRUE)
            AS anonymized,
        COALESCE(r.invocations, 0) AS invocations,
        COALESCE(r.threads, 0) AS threads,
        CASE WHEN COALESCE(r.threads, 0) > 0
            THEN r.invocations::numeric / r.threads ELSE 0 END
            AS avg_invocations_per_thread,
        COALESCE(r.total_tokens, 0) AS total_tokens,
        COALESCE(r.total_cost_usd, 0) AS total_cost_usd,
        COALESCE(r.invocations_without_cost, 0) AS invocations_without_cost,
        COALESCE(r.invocations_with_partial_cost, 0) AS invocations_with_partial_cost,
        COALESCE(r.avg_invocation_seconds, 0) AS avg_invocation_seconds,
        COALESCE(r.avg_thread_seconds, 0) AS avg_thread_seconds,
        COALESCE(m.provider_model_id, 'default') AS favorite_model,
        e.configured_effort AS favorite_model_effort,
        COALESCE(pr.prs_opened, 0) AS prs_opened,
        COALESCE(pr.merged_prs, 0) AS merged_prs,
        CASE WHEN COALESCE(r.threads, 0) > 0
            THEN COALESCE(pr.merged_prs, 0)::numeric / r.threads ELSE 0
        END AS merged_prs_per_thread,
        COALESCE(pr.additions, 0) AS additions,
        COALESCE(pr.deletions, 0) AS deletions,
        COALESCE(pr.agent_loc, 0) AS agent_loc
    FROM members p
    LEFT JOIN identity_directory d
      ON d.workspace_id = :workspace_id AND d.person_id = p.person_id
    LEFT JOIN run_totals r ON r.person_id = p.person_id
    LEFT JOIN pr_totals pr ON pr.person_id = p.person_id
    LEFT JOIN models m ON m.person_id = p.person_id
    LEFT JOIN efforts e
      ON e.person_id = p.person_id AND e.provider_model_id = m.provider_model_id
), labeled AS (
    SELECT *, regexp_replace(favorite_model, '[^A-Za-z0-9._:/+-]', '-', 'g')
        AS sanitized_model
    FROM metrics
), ranked AS (
    SELECT *,
        -- Sorting and disclosure must agree, so derive each displayed label once here
        -- and let both the ordering and the emitted row read the same column.
        -- Anonymous rows share the generic label (so ties order by rank,
        -- never a hidden name); identified rows show the stored display name
        -- or login; an identity-less row falls back to the email prefix, but
        -- only where a login would already be shown (policy off, admin, or
        -- the viewer's own row).
        CASE WHEN anonymized
            THEN 'Open SWE user'
            WHEN NULLIF(COALESCE(disclosed_login, ''), '') IS NOT NULL
            THEN COALESCE(stored_name, disclosed_login)
            ELSE COALESCE(stored_name,
                CASE WHEN NOT :anonymize_others OR :admin OR is_current
                    THEN NULLIF(split_part(email, '@', 1), '') END,
                'Open SWE user')
        END AS disclosed_name,
        -- Mirrors safeModelLabel in ui/src/lib/modelLabel.ts and the usage table's
        -- empty-label fallback.
        COALESCE(NULLIF(btrim(left(
            left(sanitized_model, strpos(sanitized_model, ':')) ||
            split_part(substr(sanitized_model, strpos(sanitized_model, ':') + 1), '/', -1),
            48
        ), '-'), ''), 'Unavailable') AS favorite_model_label,
        row_number() OVER (
            ORDER BY merged_prs DESC, agent_loc DESC, prs_opened DESC,
                COALESCE(NULLIF(display_name, ''), NULLIF(github_login, ''),
                    NULLIF(split_part(email, '@', 1), '')) NULLS LAST,
                person_id
        ) AS rank FROM labeled
), keyed AS (
    SELECT *,
        -- One key per sortable type: the inactive key is NULL for every row, so it
        -- ties and drops out of the ordering. Adding a column is a single line.
        CASE :sort
            WHEN 'user' THEN lower(disclosed_name)
            WHEN 'favorite_model' THEN lower(favorite_model_label)
        END AS text_key,
        CASE :sort
            WHEN 'rank' THEN rank::numeric
            WHEN 'invocations' THEN invocations::numeric
            WHEN 'threads' THEN threads::numeric
            WHEN 'avg_invocations_per_thread' THEN avg_invocations_per_thread
            WHEN 'total_tokens' THEN total_tokens::numeric
            WHEN 'total_cost_usd' THEN total_cost_usd::numeric
            WHEN 'avg_invocation_seconds' THEN avg_invocation_seconds::numeric
            WHEN 'avg_thread_seconds' THEN avg_thread_seconds::numeric
            WHEN 'prs_opened' THEN prs_opened::numeric
            WHEN 'merged_prs' THEN merged_prs::numeric
            WHEN 'merged_prs_per_thread' THEN merged_prs_per_thread
            WHEN 'agent_loc' THEN agent_loc::numeric
        END AS numeric_key
    FROM ranked
), ordered AS (
    SELECT *, row_number() OVER (ORDER BY
        CASE WHEN :direction = 'asc' THEN text_key END ASC,
        CASE WHEN :direction = 'desc' THEN text_key END DESC,
        CASE WHEN :direction = 'asc' THEN numeric_key END ASC,
        CASE WHEN :direction = 'desc' THEN numeric_key END DESC,
        rank
    ) AS position FROM keyed
), selected AS (
    SELECT position,
        jsonb_build_object(
            'rank', rank,
            'user', jsonb_build_object(
                'name', disclosed_name,
                'github_login', CASE WHEN :admin OR is_current THEN NULLIF(disclosed_login, '') END,
                'email', CASE WHEN is_current THEN NULLIF(email, '') END,
                'avatar_url', CASE WHEN :admin OR is_current
                    THEN CASE WHEN NULLIF(disclosed_login, '') IS NOT NULL
                        THEN 'https://github.com/' || disclosed_login || '.png?size=80' END
                END),
            'favorite_model', favorite_model,
            'favorite_model_effort', favorite_model_effort,
            'avg_invocation_seconds', avg_invocation_seconds,
            'avg_thread_seconds', avg_thread_seconds,
            'avg_run_seconds', avg_invocation_seconds,
            'agent_runs', invocations, 'invocations', invocations, 'threads', threads,
            'avg_invocations_per_thread', avg_invocations_per_thread,
            'prs_opened', prs_opened, 'merged_prs', merged_prs,
            'merged_prs_per_thread', merged_prs_per_thread,
            'agent_loc', agent_loc, 'additions', additions, 'deletions', deletions,
            'total_tokens', total_tokens, 'total_cost_usd', total_cost_usd,
            'invocations_without_cost', invocations_without_cost,
            'invocations_with_partial_cost', invocations_with_partial_cost
        ) AS row
    FROM ordered WHERE position > :offset AND position <= :offset + :limit
)
SELECT (SELECT COALESCE(jsonb_agg(row ORDER BY position), '[]'::jsonb) FROM selected) AS rows,
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
    offset: int = 0,
    cursor: str | None = None,
    sort: UsageSort = "rank",
    direction: SortDirection = "asc",
    admin: bool = False,
    anonymize_others: bool = False,
) -> dict[str, Any]:
    """Read usage and review cohorts from one bounded PostgreSQL snapshot."""
    normalized = period if period in {"7d", "30d", "all"} else "30d"
    workspace = workspace_id()
    if cursor:
        as_of, offset = _decode_usage_cursor(cursor, workspace, normalized, sort, direction)
    else:
        as_of = datetime.now(UTC)
    generated_at_ms = int(as_of.timestamp() * 1000)
    parameters = {
        "workspace_id": workspace,
        "as_of": as_of,
        "limit": min(max(limit, 1), 100),
        "offset": max(offset, 0),
        "current_login": (current_login or "").strip().lower(),
        "current_email": (current_email or "").strip().lower(),
        "admin": admin,
        "anonymize_others": anonymize_others,
        "sort": sort,
        "direction": direction,
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
        "next_cursor": (
            _encode_usage_cursor(
                as_of,
                parameters["offset"] + len(usage["rows"]),
                workspace,
                normalized,
                sort,
                direction,
            )
            if len(usage["rows"]) == parameters["limit"]
            and parameters["offset"] + len(usage["rows"]) < usage["total_members"]
            else None
        ),
        "generated_at_ms": generated_at_ms,
        "reviewer_stats": reviewer,
        **metadata,
        "as_of": as_of.isoformat(),
        "build_info": backend_build_info(),
    }
