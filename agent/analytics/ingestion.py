"""Idempotent ingestion, projection, and summary invalidation."""

import json
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import BigInteger, bindparam, text
from sqlalchemy.ext.asyncio import AsyncConnection

from agent.analytics.database import transaction
from agent.analytics.events import EventEnvelope, EventName
from agent.config import ENV

_INSERT_ID = text(
    "INSERT INTO event_ids(event_id, occurred_at) VALUES (:event_id, :occurred_at) "
    "ON CONFLICT (event_id) DO NOTHING RETURNING event_id"
)
_INSERT_EVENT = text(
    """
    INSERT INTO events (
        event_id, event_name, schema_version, occurred_at, recorded_at, workspace_id,
        environment, producer, producer_event_id, source_version, correlation_id,
        causation_id, run_id, preparation_run_id, thread_id, task_id, pr_id, review_id,
        finding_id, user_id, team_id, repository_id, model_id, entry_point,
        privacy_classification, payload
    ) VALUES (
        :event_id, :event_name, :schema_version, :occurred_at, :recorded_at, :workspace_id,
        :environment, :producer, :producer_event_id, :source_version, :correlation_id,
        :causation_id, :run_id, :preparation_run_id, :thread_id, :task_id, :pr_id, :review_id,
        :finding_id, :user_id, :team_id, :repository_id, :model_id, :entry_point,
        :privacy_classification, CAST(:payload AS jsonb)
    )
    """
)


def _params(event: EventEnvelope) -> dict[str, object]:
    data = event.model_dump(mode="python")
    data["event_name"] = event.event_name.value
    data["entry_point"] = event.entry_point.value
    data["payload"] = json.dumps(event.payload.model_dump(mode="json"), separators=(",", ":"))
    return data


def _epoch() -> datetime:
    raw = ENV.ANALYTICS_EPOCH.optional()
    if raw is None:
        raise RuntimeError("ANALYTICS_EPOCH is required")
    parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("ANALYTICS_EPOCH must be timezone-aware")
    return parsed


async def ingest(event: EventEnvelope) -> bool:
    if event.occurred_at < _epoch():
        raise ValueError("event occurred before the configured analytics epoch")
    async with transaction() as conn:
        subject_id = event.finding_id or event.pr_id
        if subject_id is not None:
            # Serialize creation and outcomes even before a projection row exists.
            await conn.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:subject, 0))"),
                {"subject": f"analytics:{event.workspace_id}:{subject_id}"},
            )
        inserted = await conn.scalar(
            _INSERT_ID, {"event_id": event.event_id, "occurred_at": event.occurred_at}
        )
        if inserted is None:
            return False
        await conn.execute(_INSERT_EVENT, _params(event))
        await conn.execute(
            text(
                "INSERT INTO ingestion_receipts (workspace_id, producer, producer_event_id, "
                "event_name, event_id, expires_at) VALUES (:workspace_id, :producer, "
                ":producer_event_id, :event_name, :event_id, clock_timestamp() + "
                "(:receipt_days * interval '1 day')) ON CONFLICT DO NOTHING"
            ),
            {
                "workspace_id": event.workspace_id,
                "producer": event.producer,
                "producer_event_id": event.producer_event_id,
                "event_name": event.event_name.value,
                "event_id": event.event_id,
                "receipt_days": ENV.ANALYTICS_RECEIPT_DAYS.get_int(90),
            },
        )
        await _project(conn, event)
        await _mark_dirty(conn, event)
    return True


async def _project(conn: AsyncConnection, event: EventEnvelope) -> None:
    name = event.event_name
    if name == EventName.RUN_STARTED:
        payload = event.payload.model_dump(mode="json")
        await conn.execute(
            text(
                """
                INSERT INTO run_projection (
                    workspace_id, run_id, preparation_run_id, thread_id, task_id, user_id,
                    team_id, repository_id, configured_model_id, effective_model_id,
                    model_attribution_quality, entry_point, started_at
                ) VALUES (
                    :workspace_id, :run_id, :preparation_run_id, :thread_id, :task_id, :user_id,
                    :team_id, :repository_id, :configured_model_id, :effective_model_id,
                    :quality, :entry_point, :occurred_at
                ) ON CONFLICT (workspace_id, run_id) DO UPDATE SET
                    started_at = LEAST(run_projection.started_at, EXCLUDED.started_at),
                    configured_model_id = COALESCE(EXCLUDED.configured_model_id, run_projection.configured_model_id),
                    effective_model_id = COALESCE(EXCLUDED.effective_model_id, run_projection.effective_model_id),
                    model_attribution_quality = CASE WHEN EXCLUDED.model_attribution_quality = 'effective'
                        THEN EXCLUDED.model_attribution_quality ELSE run_projection.model_attribution_quality END,
                    entry_point = CASE WHEN run_projection.entry_point = 'unknown'
                        THEN EXCLUDED.entry_point ELSE run_projection.entry_point END,
                    thread_id = COALESCE(run_projection.thread_id, EXCLUDED.thread_id),
                    task_id = COALESCE(run_projection.task_id, EXCLUDED.task_id),
                    user_id = COALESCE(run_projection.user_id, EXCLUDED.user_id),
                    team_id = COALESCE(run_projection.team_id, EXCLUDED.team_id),
                    repository_id = COALESCE(run_projection.repository_id, EXCLUDED.repository_id),
                    updated_at = clock_timestamp()
                """
            ),
            {
                **_identity_params(event),
                "configured_model_id": payload.get("configured_model_id"),
                "effective_model_id": payload.get("effective_model_id"),
                "quality": payload["model_attribution_quality"],
                "entry_point": event.entry_point.value,
                "occurred_at": event.occurred_at,
            },
        )
    elif name in {EventName.RUN_COMPLETED, EventName.RUN_FAILED, EventName.RUN_CANCELED}:
        await _project_run_terminal(conn, event)
    elif name == EventName.RUN_COST_RECORDED:
        payload = event.payload.model_dump()
        await conn.execute(
            text(
                """
                INSERT INTO latest_cost_projection (
                    workspace_id, run_id, observation_revision, observed_at, status, cost_usd,
                    input_tokens, output_tokens, total_tokens, source, missing_reason, event_id
                ) VALUES (
                    :workspace_id, :run_id, :revision, :observed_at, :status, :cost_usd,
                    :input_tokens, :output_tokens, :total_tokens, :source, :missing_reason, :event_id
                ) ON CONFLICT (workspace_id, run_id) DO UPDATE SET
                    observation_revision = EXCLUDED.observation_revision,
                    observed_at = EXCLUDED.observed_at, status = EXCLUDED.status,
                    cost_usd = EXCLUDED.cost_usd, input_tokens = EXCLUDED.input_tokens,
                    output_tokens = EXCLUDED.output_tokens, total_tokens = EXCLUDED.total_tokens,
                    source = EXCLUDED.source, missing_reason = EXCLUDED.missing_reason,
                    event_id = EXCLUDED.event_id
                WHERE EXCLUDED.observation_revision > latest_cost_projection.observation_revision
                   OR (EXCLUDED.observation_revision = latest_cost_projection.observation_revision
                       AND EXCLUDED.observed_at > latest_cost_projection.observed_at)
                """
            ),
            {
                "workspace_id": event.workspace_id,
                "run_id": event.run_id,
                "revision": payload["observation_revision"],
                "observed_at": payload["observed_at"],
                "status": payload["status"],
                "cost_usd": payload.get("cost_usd"),
                "input_tokens": payload.get("input_tokens"),
                "output_tokens": payload.get("output_tokens"),
                "total_tokens": payload.get("total_tokens"),
                "source": payload["source"],
                "missing_reason": payload.get("missing_reason"),
                "event_id": event.event_id,
            },
        )
    elif name == EventName.PR_OPENED:
        payload = event.payload.model_dump(mode="json")
        await conn.execute(
            text(
                """
                INSERT INTO pr_projection (
                    workspace_id, pr_id, repository_id, opening_run_id, originating_model_id,
                    model_attribution_quality, opened_at, latest_transition_at, current_state, source_version,
                    repository_private
                ) VALUES (
                    :workspace_id, :pr_id, :repository_id, :opening_run_id, :model_id,
                    :quality, :occurred_at, :occurred_at, 'open', :source_version, :repository_private
                ) ON CONFLICT (workspace_id, pr_id) DO NOTHING
                """
            ),
            {
                "workspace_id": event.workspace_id,
                "pr_id": event.pr_id,
                "repository_id": event.repository_id,
                "opening_run_id": payload["opening_run_id"],
                "model_id": payload.get("originating_model_id"),
                "quality": payload["model_attribution_quality"],
                "occurred_at": event.occurred_at,
                "source_version": event.source_version,
                "repository_private": payload.get("repository_private"),
            },
        )
        await _reconcile_outcomes(conn, event)
    elif name == EventName.PR_RUN_LINKED:
        await conn.execute(
            text(
                "INSERT INTO pr_run_link_projection (workspace_id, pr_id, run_id, link_role, "
                "linked_at) VALUES (:workspace_id, :pr_id, :run_id, :link_role, :occurred_at) "
                "ON CONFLICT DO NOTHING"
            ),
            {
                "workspace_id": event.workspace_id,
                "pr_id": event.pr_id,
                "run_id": event.run_id,
                "link_role": event.payload.model_dump()["link_role"],
                "occurred_at": event.occurred_at,
            },
        )
    elif name == EventName.FEEDBACK_SUBMITTED:
        payload = event.payload.model_dump(mode="json")
        await conn.execute(
            text(
                "INSERT INTO feedback_projection (workspace_id, feedback_id, run_id, task_id, "
                "user_id, sentiment, rating, submitted_at) VALUES (:workspace_id, :feedback_id, "
                ":run_id, :task_id, :user_id, :sentiment, :rating, :occurred_at) ON CONFLICT "
                "(workspace_id, feedback_id) DO NOTHING"
            ),
            {
                "workspace_id": event.workspace_id,
                "feedback_id": event.event_id,
                "run_id": event.run_id,
                "task_id": event.task_id,
                "user_id": event.user_id,
                "sentiment": payload["sentiment"],
                "rating": payload.get("rating"),
                "occurred_at": event.occurred_at,
            },
        )
    elif name == EventName.FEEDBACK_WITHDRAWN:
        await conn.execute(
            text(
                "UPDATE feedback_projection SET withdrawn_at = :occurred_at WHERE workspace_id "
                "= :workspace_id AND feedback_id = :feedback_id AND withdrawn_at IS NULL"
            ),
            {
                "occurred_at": event.occurred_at,
                "workspace_id": event.workspace_id,
                "feedback_id": event.payload.model_dump()["submission_event_id"],
            },
        )
    elif name in {
        EventName.TASK_MARKED_COMPLETE,
        EventName.TASK_ACCEPTED,
        EventName.TASK_REWORK_REQUESTED,
    }:
        payload = event.payload.model_dump()
        await conn.execute(
            text(
                "INSERT INTO task_projection (workspace_id, task_id, thread_id, user_id, "
                "marked_complete_at, accepted_at, major_rework_count, minor_rework_count) VALUES "
                "(:workspace_id, :task_id, :thread_id, :user_id, :complete_at, :accepted_at, "
                ":major, :minor) ON CONFLICT (workspace_id, task_id) DO UPDATE SET "
                "thread_id = COALESCE(task_projection.thread_id, EXCLUDED.thread_id), user_id = "
                "COALESCE(task_projection.user_id, EXCLUDED.user_id), marked_complete_at = "
                "COALESCE(task_projection.marked_complete_at, EXCLUDED.marked_complete_at), "
                "accepted_at = COALESCE(task_projection.accepted_at, EXCLUDED.accepted_at), "
                "major_rework_count = task_projection.major_rework_count + EXCLUDED.major_rework_count, "
                "minor_rework_count = task_projection.minor_rework_count + EXCLUDED.minor_rework_count, "
                "updated_at = clock_timestamp()"
            ),
            {
                "workspace_id": event.workspace_id,
                "task_id": event.task_id,
                "thread_id": event.thread_id,
                "user_id": event.user_id,
                "complete_at": event.occurred_at
                if name == EventName.TASK_MARKED_COMPLETE
                else None,
                "accepted_at": event.occurred_at if name == EventName.TASK_ACCEPTED else None,
                "major": 1
                if name == EventName.TASK_REWORK_REQUESTED and payload["scope"] == "major"
                else 0,
                "minor": 1
                if name == EventName.TASK_REWORK_REQUESTED and payload["scope"] == "minor"
                else 0,
            },
        )
    elif name in {
        EventName.PR_MERGED,
        EventName.PR_CLOSED_WITHOUT_MERGE,
        EventName.PR_REOPENED,
    }:
        state = {
            EventName.PR_MERGED: "merged",
            EventName.PR_CLOSED_WITHOUT_MERGE: "closed_without_merge",
            EventName.PR_REOPENED: "open",
        }[name]
        await conn.execute(
            text(
                "UPDATE pr_projection SET current_state = :state, outcome_at = CASE WHEN "
                ":state = 'open' THEN NULL ELSE :occurred_at END, source_version = :source_version, "
                "latest_transition_at = :occurred_at, updated_at = clock_timestamp() "
                "WHERE workspace_id = :workspace_id AND pr_id = :pr_id "
                "AND ((:source_version IS NOT NULL AND (source_version IS NULL OR :source_version > source_version)) "
                "OR (:source_version IS NULL AND source_version IS NULL AND "
                "(latest_transition_at IS NULL OR :occurred_at >= latest_transition_at)))"
            ).bindparams(bindparam("source_version", type_=BigInteger)),
            {
                "state": state,
                "occurred_at": event.occurred_at,
                "source_version": event.source_version,
                "workspace_id": event.workspace_id,
                "pr_id": event.pr_id,
            },
        )
    elif name == EventName.REVIEW_PUBLISHED:
        await conn.execute(
            text(
                "INSERT INTO review_projection (workspace_id, review_id, pr_id, repository_id, "
                "published_at, finding_count) VALUES (:workspace_id, :review_id, :pr_id, "
                ":repository_id, :occurred_at, :finding_count) ON CONFLICT DO NOTHING"
            ),
            {
                "workspace_id": event.workspace_id,
                "review_id": event.review_id,
                "pr_id": event.pr_id,
                "repository_id": event.repository_id,
                "occurred_at": event.occurred_at,
                "finding_count": event.payload.model_dump()["finding_count"],
            },
        )
    elif name == EventName.FINDING_SURFACED:
        payload = event.payload.model_dump()
        await conn.execute(
            text(
                "INSERT INTO finding_projection (workspace_id, finding_id, review_id, pr_id, "
                "repository_id, severity, category, surfaced_at) VALUES (:workspace_id, "
                ":finding_id, :review_id, :pr_id, :repository_id, :severity, :category, "
                ":occurred_at) ON CONFLICT (workspace_id, finding_id) DO UPDATE SET surfaced_at = "
                "COALESCE(finding_projection.surfaced_at, EXCLUDED.surfaced_at), updated_at = clock_timestamp()"
            ),
            {
                "workspace_id": event.workspace_id,
                "finding_id": event.finding_id,
                "review_id": event.review_id,
                "pr_id": event.pr_id,
                "repository_id": event.repository_id,
                "severity": payload["severity"],
                "category": payload["category"],
                "occurred_at": event.occurred_at,
            },
        )
        await _reconcile_finding(conn, event)
    elif name in {
        EventName.FINDING_RESOLVED,
        EventName.FINDING_DISMISSED,
        EventName.FINDING_REOPENED,
    }:
        await _reconcile_finding(conn, event)


async def _reconcile_finding(conn: AsyncConnection, event: EventEnvelope) -> None:
    await conn.execute(
        text(
            """
            WITH transitions AS (
                SELECT event_name, occurred_at, source_version, event_id FROM events
                WHERE workspace_id = :workspace_id AND finding_id = :finding_id
                  AND event_name IN ('finding.resolved', 'finding.dismissed', 'finding.reopened')
            ), latest AS (
                SELECT * FROM transitions
                ORDER BY source_version DESC NULLS LAST, occurred_at DESC, event_id DESC LIMIT 1
            ), history AS (
                SELECT max(occurred_at) FILTER (WHERE event_name = 'finding.resolved') AS resolved_at,
                       max(occurred_at) FILTER (WHERE event_name = 'finding.dismissed') AS dismissed_at,
                       count(*) FILTER (WHERE event_name = 'finding.reopened') AS reopened_count
                FROM transitions
            )
            UPDATE finding_projection SET
                current_state = CASE latest.event_name
                    WHEN 'finding.resolved' THEN 'resolved'
                    WHEN 'finding.dismissed' THEN 'dismissed' ELSE 'open' END,
                source_version = latest.source_version,
                latest_occurred_at = latest.occurred_at,
                resolved_at = history.resolved_at,
                dismissed_at = history.dismissed_at,
                reopened_count = history.reopened_count,
                updated_at = clock_timestamp()
            FROM latest, history
            WHERE workspace_id = :workspace_id AND finding_id = :finding_id
            """
        ),
        {"workspace_id": event.workspace_id, "finding_id": event.finding_id},
    )


async def _reconcile_outcomes(conn: AsyncConnection, event: EventEnvelope) -> None:
    result = await conn.execute(
        text(
            "SELECT * FROM events WHERE workspace_id = :workspace_id AND pr_id = :pr_id "
            "AND event_name = ANY(:names) "
            "ORDER BY source_version ASC NULLS FIRST, occurred_at, event_id"
        ),
        {
            "workspace_id": event.workspace_id,
            "pr_id": event.pr_id,
            "names": [
                EventName.PR_MERGED.value,
                EventName.PR_CLOSED_WITHOUT_MERGE.value,
                EventName.PR_REOPENED.value,
            ],
        },
    )
    for row in result.mappings():
        await _project(conn, EventEnvelope.model_validate(dict(row)))


async def _project_run_terminal(conn: AsyncConnection, event: EventEnvelope) -> None:
    requested = {
        EventName.RUN_COMPLETED: "completed",
        EventName.RUN_FAILED: "failed",
        EventName.RUN_CANCELED: "canceled",
    }[event.event_name]
    payload = event.payload.model_dump()
    input_tokens = payload.get("input_tokens")
    output_tokens = payload.get("output_tokens")
    total_tokens = payload.get("total_tokens")
    current = await conn.execute(
        text(
            "SELECT technical_status, terminal_event_ids FROM run_projection WHERE workspace_id = "
            ":workspace_id AND run_id = :run_id FOR UPDATE"
        ),
        {"workspace_id": event.workspace_id, "run_id": event.run_id},
    )
    row = current.mappings().one_or_none()
    if row is None:
        await conn.execute(
            text(
                "INSERT INTO run_projection (workspace_id, run_id, technical_status, terminal_at, "
                "terminal_event_ids, input_tokens, output_tokens, total_tokens) VALUES "
                "(:workspace_id, :run_id, :status, :occurred_at, ARRAY[:event_id]::uuid[], "
                ":input_tokens, :output_tokens, :total_tokens)"
            ),
            {
                "workspace_id": event.workspace_id,
                "run_id": event.run_id,
                "status": requested,
                "occurred_at": event.occurred_at,
                "event_id": event.event_id,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "total_tokens": total_tokens,
            },
        )
        return
    current_status = row["technical_status"]
    event_ids = list(row["terminal_event_ids"] or [])
    if event.event_id not in event_ids:
        event_ids.append(event.event_id)
    conflict = current_status not in {"pending", requested}
    status = "conflicted" if conflict else requested
    await conn.execute(
        text(
            "UPDATE run_projection SET technical_status = :status, terminal_at = LEAST(COALESCE("
            "terminal_at, :occurred_at), :occurred_at), terminal_conflict = :conflict, "
            "terminal_event_ids = :event_ids, input_tokens = COALESCE(:input_tokens, input_tokens), "
            "output_tokens = COALESCE(:output_tokens, output_tokens), total_tokens = COALESCE("
            ":total_tokens, total_tokens), updated_at = "
            "clock_timestamp() WHERE workspace_id = :workspace_id AND run_id = :run_id"
        ),
        {
            "status": status,
            "occurred_at": event.occurred_at,
            "conflict": conflict,
            "event_ids": event_ids,
            "workspace_id": event.workspace_id,
            "run_id": event.run_id,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": total_tokens,
        },
    )
    if conflict:
        await conn.execute(
            text(
                "INSERT INTO projection_conflicts (workspace_id, subject_type, subject_id, "
                "conflict_type, event_ids) VALUES (:workspace_id, 'run', :run_id, "
                "'contradictory_terminal_state', :event_ids) ON CONFLICT (workspace_id, "
                "subject_type, subject_id, conflict_type) DO UPDATE SET event_ids = EXCLUDED.event_ids, "
                "detected_at = clock_timestamp(), resolved_at = NULL"
            ),
            {
                "workspace_id": event.workspace_id,
                "run_id": event.run_id,
                "event_ids": event_ids,
            },
        )


def _identity_params(event: EventEnvelope) -> dict[str, UUID | None]:
    return {
        "workspace_id": event.workspace_id,
        "run_id": event.run_id,
        "preparation_run_id": event.preparation_run_id,
        "thread_id": event.thread_id,
        "task_id": event.task_id,
        "user_id": event.user_id,
        "team_id": event.team_id,
        "repository_id": event.repository_id,
    }


async def _mark_dirty(conn: AsyncConnection, event: EventEnvelope) -> None:
    version = ENV.ANALYTICS_SUMMARY_VERSION.get_int(1)
    families = {"additive", "distinct_membership"}
    dates = {event.occurred_at.astimezone(UTC).date()}
    if event.pr_id is not None:
        families.add("pr_open_cohort")
        opened_at = await conn.scalar(
            text(
                "SELECT opened_at FROM pr_projection WHERE workspace_id = :workspace_id AND pr_id = :pr_id"
            ),
            {"workspace_id": event.workspace_id, "pr_id": event.pr_id},
        )
        if opened_at is not None:
            dates.add(opened_at.astimezone(UTC).date())
    if event.finding_id is not None:
        families.update({"finding_surfaced_cohort", "latency_histogram"})
        surfaced_at = await conn.scalar(
            text(
                "SELECT surfaced_at FROM finding_projection WHERE workspace_id = :workspace_id "
                "AND finding_id = :finding_id"
            ),
            {"workspace_id": event.workspace_id, "finding_id": event.finding_id},
        )
        if surfaced_at is not None:
            dates.add(surfaced_at.astimezone(UTC).date())
    if event.event_name in {EventName.RUN_STARTED, EventName.RUN_COST_RECORDED}:
        families.add("cost_completeness")
        started_at = await conn.scalar(
            text(
                "SELECT started_at FROM run_projection WHERE workspace_id = :workspace_id "
                "AND run_id = :run_id"
            ),
            {"workspace_id": event.workspace_id, "run_id": event.run_id},
        )
        if started_at is not None:
            dates.add(started_at.astimezone(UTC).date())
    for family in families:
        for partition_date in dates:
            await conn.execute(
                text(
                    "INSERT INTO dirty_summary_partitions (workspace_id, summary_version, family, "
                    "partition_date, reason_event_id) VALUES (:workspace_id, :version, :family, "
                    ":partition_date, :event_id) ON CONFLICT (workspace_id, summary_version, "
                    "family, partition_date, dimension_key) DO UPDATE SET dirty_since = "
                    "LEAST(dirty_summary_partitions.dirty_since, clock_timestamp()), reason_event_id = "
                    "EXCLUDED.reason_event_id"
                ),
                {
                    "workspace_id": event.workspace_id,
                    "version": version,
                    "family": family,
                    "partition_date": partition_date,
                    "event_id": event.event_id,
                },
            )
