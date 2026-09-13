"""Idempotent ingestion and durable event projections."""

import json
from datetime import UTC
from uuid import UUID

from sqlalchemy import BigInteger, bindparam, text
from sqlalchemy.ext.asyncio import AsyncConnection

from agent.analytics.events import EventEnvelope, EventName
from agent.config import ENV
from agent.database.analytics import record_capture, transaction

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


async def ingest(event: EventEnvelope) -> bool:
    async with transaction() as conn:
        subject_id = event.finding_id or event.pr_id
        if event.event_name == EventName.FEEDBACK_SUBMITTED:
            subject_id = event.event_id
        elif event.event_name == EventName.FEEDBACK_WITHDRAWN:
            subject_id = event.payload.model_dump()["submission_event_id"]
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
        await record_capture(conn)
        await conn.execute(
            text(
                "INSERT INTO additive_event_projection (workspace_id, partition_date, event_name, "
                "event_count) VALUES (:workspace_id, :partition_date, :event_name, 1) "
                "ON CONFLICT (workspace_id, partition_date, event_name) DO UPDATE SET "
                "event_count = additive_event_projection.event_count + 1"
            ),
            {
                "workspace_id": event.workspace_id,
                "partition_date": event.occurred_at.astimezone(UTC).date(),
                "event_name": event.event_name.value,
            },
        )
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
        await conn.execute(
            text("UPDATE deployment_metadata SET last_processed_at = clock_timestamp()")
        )
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
                        OR run_projection.model_attribution_quality = 'unavailable'
                        THEN EXCLUDED.model_attribution_quality ELSE run_projection.model_attribution_quality END,
                    entry_point = CASE WHEN run_projection.entry_point = 'unknown'
                        THEN EXCLUDED.entry_point ELSE run_projection.entry_point END,
                    preparation_run_id = COALESCE(run_projection.preparation_run_id, EXCLUDED.preparation_run_id),
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
    elif name == EventName.PR_OBSERVED:
        await _project_pr_usage(conn, event)
    elif name == EventName.FINDING_OBSERVED:
        await _project_finding_usage(conn, event)
        await _reconcile_observed_finding(conn, event)
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
                "user_id, sentiment, rating, submitted_at, withdrawn_at) VALUES (:workspace_id, :feedback_id, "
                ":run_id, :task_id, :user_id, :sentiment, :rating, :occurred_at, "
                "(SELECT withdrawn_at FROM feedback_withdrawal_projection WHERE workspace_id = "
                ":workspace_id AND feedback_id = :feedback_id)) ON CONFLICT "
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
        params = {
            "occurred_at": event.occurred_at,
            "workspace_id": event.workspace_id,
            "feedback_id": event.payload.model_dump()["submission_event_id"],
        }
        await conn.execute(
            text(
                "INSERT INTO feedback_withdrawal_projection (workspace_id, feedback_id, withdrawn_at) "
                "VALUES (:workspace_id, :feedback_id, :occurred_at) "
                "ON CONFLICT (workspace_id, feedback_id) DO UPDATE SET withdrawn_at = "
                "LEAST(feedback_withdrawal_projection.withdrawn_at, EXCLUDED.withdrawn_at)"
            ),
            params,
        )
        await conn.execute(
            text(
                "UPDATE feedback_projection SET withdrawn_at = LEAST(withdrawn_at, :occurred_at) "
                "WHERE workspace_id = :workspace_id AND feedback_id = :feedback_id"
            ),
            params,
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
            ), candidate AS (
                SELECT latest.*,
                    f.latest_occurred_at IS NULL
                    OR (latest.source_version IS NOT NULL AND
                        (f.source_version IS NULL OR latest.source_version > f.source_version))
                    OR (latest.source_version IS NOT DISTINCT FROM f.source_version
                        AND latest.occurred_at >= f.latest_occurred_at) AS replace_state
                FROM latest CROSS JOIN finding_projection f
                WHERE f.workspace_id = :workspace_id AND f.finding_id = :finding_id
            ), history AS (
                SELECT max(occurred_at) FILTER (WHERE event_name = 'finding.resolved') AS resolved_at,
                       max(occurred_at) FILTER (WHERE event_name = 'finding.dismissed') AS dismissed_at,
                       count(*) FILTER (WHERE event_name = 'finding.reopened') AS reopened_count
                FROM transitions
            )
            UPDATE finding_projection SET
                current_state = CASE WHEN NOT candidate.replace_state THEN current_state
                    ELSE CASE candidate.event_name
                    WHEN 'finding.resolved' THEN 'resolved'
                    WHEN 'finding.dismissed' THEN 'dismissed' ELSE 'open' END END,
                source_version = CASE WHEN candidate.replace_state
                    THEN candidate.source_version ELSE finding_projection.source_version END,
                latest_occurred_at = CASE WHEN candidate.replace_state
                    THEN candidate.occurred_at ELSE latest_occurred_at END,
                -- Raw events expire after the retention window; the durable baseline
                -- columns keep prior history from being erased by a post-retention
                -- rebuild, and absorb every newly observed transition.
                resolved_at = GREATEST(
                    COALESCE(history.resolved_at, history_baseline_resolved_at),
                    COALESCE(history_baseline_resolved_at, history.resolved_at)
                ),
                dismissed_at = GREATEST(
                    COALESCE(history.dismissed_at, history_baseline_dismissed_at),
                    COALESCE(history_baseline_dismissed_at, history.dismissed_at)
                ),
                reopened_count = GREATEST(
                    history.reopened_count,
                    GREATEST(finding_projection.reopened_count, history_baseline_reopened_count) + :new_reopening
                ),
                history_baseline_resolved_at = GREATEST(
                    COALESCE(history.resolved_at, history_baseline_resolved_at),
                    COALESCE(history_baseline_resolved_at, history.resolved_at)
                ),
                history_baseline_dismissed_at = GREATEST(
                    COALESCE(history.dismissed_at, history_baseline_dismissed_at),
                    COALESCE(history_baseline_dismissed_at, history.dismissed_at)
                ),
                history_baseline_reopened_count = GREATEST(
                    history.reopened_count,
                    GREATEST(finding_projection.reopened_count, history_baseline_reopened_count) + :new_reopening
                ),
                updated_at = clock_timestamp()
            FROM candidate, history
            WHERE workspace_id = :workspace_id AND finding_id = :finding_id
            """
        ),
        {
            "workspace_id": event.workspace_id,
            "finding_id": event.finding_id,
            "new_reopening": int(event.event_name == EventName.FINDING_REOPENED),
        },
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


async def _project_pr_usage(conn: AsyncConnection, event: EventEnvelope) -> None:
    await conn.execute(
        text("""
            INSERT INTO pr_usage_projection (
                workspace_id, pr_id, user_id, additions, deletions, changed_files,
                observed_at, source_version, event_id
            ) VALUES (
                :workspace_id, :pr_id, :user_id, :additions, :deletions, :changed_files,
                :observed_at, :source_version, :event_id
            ) ON CONFLICT (workspace_id, pr_id) DO UPDATE SET
                user_id = COALESCE(EXCLUDED.user_id, pr_usage_projection.user_id),
                observed_at = EXCLUDED.observed_at,
                source_version = EXCLUDED.source_version,
                event_id = EXCLUDED.event_id
            WHERE CASE WHEN EXCLUDED.source_version IS NOT NULL
                        AND pr_usage_projection.source_version IS NOT NULL
                THEN (EXCLUDED.source_version, EXCLUDED.observed_at, EXCLUDED.event_id)
                    > (pr_usage_projection.source_version, pr_usage_projection.observed_at,
                       pr_usage_projection.event_id)
                ELSE (EXCLUDED.observed_at, EXCLUDED.event_id)
                    > (pr_usage_projection.observed_at, pr_usage_projection.event_id) END
        """),
        {
            "workspace_id": event.workspace_id,
            "pr_id": event.pr_id,
            "user_id": event.user_id,
            "observed_at": event.occurred_at,
            "source_version": event.source_version,
            "event_id": event.event_id,
            **event.payload.model_dump(),
        },
    )
    # Sparse GitHub payloads advance only the measurements they actually contain.
    for metric in ("additions", "deletions", "changed_files"):
        value = event.payload.model_dump()[metric]
        if value is None:
            continue
        await conn.execute(
            text(f"""
                UPDATE pr_usage_projection SET
                    {metric} = :value,
                    {metric}_observed_at = :observed_at,
                    {metric}_source_version = :source_version,
                    {metric}_event_id = :event_id
                WHERE workspace_id = :workspace_id AND pr_id = :pr_id
                    AND ({metric}_event_id IS NULL OR CASE
                        WHEN CAST(:source_version AS bigint) IS NOT NULL
                            AND {metric}_source_version IS NOT NULL
                        THEN (:source_version, :observed_at, :event_id)
                            > ({metric}_source_version, {metric}_observed_at, {metric}_event_id)
                        ELSE (:observed_at, :event_id) > ({metric}_observed_at, {metric}_event_id)
                    END)
            """),
            {
                "workspace_id": event.workspace_id,
                "pr_id": event.pr_id,
                "value": value,
                "observed_at": event.occurred_at,
                "source_version": event.source_version,
                "event_id": event.event_id,
            },
        )
    # Ownership may be captured separately and earlier than the GitHub statistics.
    if event.user_id is not None:
        await conn.execute(
            text(
                "UPDATE pr_usage_projection SET user_id = :user_id "
                "WHERE workspace_id = :workspace_id AND pr_id = :pr_id AND user_id IS NULL"
            ),
            {"workspace_id": event.workspace_id, "pr_id": event.pr_id, "user_id": event.user_id},
        )


async def _project_finding_usage(conn: AsyncConnection, event: EventEnvelope) -> None:
    params = {
        "workspace_id": event.workspace_id,
        "finding_id": event.finding_id,
        "review_id": event.review_id,
        "pr_id": event.pr_id,
        "repository_id": event.repository_id,
        "observed_at": event.occurred_at,
        "source_version": event.source_version,
        "event_id": event.event_id,
        **event.payload.model_dump(),
    }
    await conn.execute(
        text("""
            INSERT INTO finding_usage_projection (
                workspace_id, finding_id, review_id, pr_id, repository_id,
                recorded_at, surfaced_at, resolved_at, current_state, severity, category,
                first_seen_revision_id, resolved_revision_id, human_replies,
                observed_at, source_version, event_id
            ) VALUES (
                :workspace_id, :finding_id, :review_id, :pr_id, :repository_id,
                :recorded_at, :surfaced_at, :resolved_at, :current_state, :severity, :category,
                :first_seen_revision_id, :resolved_revision_id, :human_replies,
                :observed_at, :source_version, :event_id
            ) ON CONFLICT (workspace_id, finding_id) DO UPDATE SET
                current_state = EXCLUDED.current_state,
                severity = EXCLUDED.severity, category = EXCLUDED.category,
                first_seen_revision_id = COALESCE(EXCLUDED.first_seen_revision_id,
                    finding_usage_projection.first_seen_revision_id),
                human_replies = EXCLUDED.human_replies,
                observed_at = EXCLUDED.observed_at,
                source_version = EXCLUDED.source_version,
                event_id = EXCLUDED.event_id
            WHERE CASE WHEN EXCLUDED.source_version IS NOT NULL
                        AND finding_usage_projection.source_version IS NOT NULL
                THEN (EXCLUDED.source_version, EXCLUDED.observed_at, EXCLUDED.event_id)
                    > (finding_usage_projection.source_version, finding_usage_projection.observed_at,
                       finding_usage_projection.event_id)
                ELSE (EXCLUDED.observed_at, EXCLUDED.event_id)
                    > (finding_usage_projection.observed_at, finding_usage_projection.event_id) END
        """),
        params,
    )
    # First-occurrence milestones remain useful even when delivered after a newer observation.
    await conn.execute(
        text("""
            UPDATE finding_usage_projection SET
                recorded_at = LEAST(recorded_at, :recorded_at),
                surfaced_at = LEAST(surfaced_at, :surfaced_at),
                resolved_revision_id = CASE
                    WHEN resolved_at IS NULL OR :resolved_at < resolved_at
                    THEN COALESCE(:resolved_revision_id, resolved_revision_id)
                    ELSE resolved_revision_id END,
                resolved_at = LEAST(resolved_at, :resolved_at)
            WHERE workspace_id = :workspace_id AND finding_id = :finding_id
        """),
        params,
    )


async def _reconcile_observed_finding(conn: AsyncConnection, event: EventEnvelope) -> None:
    params = {"workspace_id": event.workspace_id, "finding_id": event.finding_id}
    await conn.execute(
        text("""
            INSERT INTO finding_projection (
                workspace_id, finding_id, review_id, pr_id, repository_id, severity, category,
                surfaced_at
            ) SELECT workspace_id, finding_id, review_id, pr_id, repository_id, severity, category,
                surfaced_at FROM finding_usage_projection
            WHERE workspace_id = :workspace_id AND finding_id = :finding_id
                AND surfaced_at IS NOT NULL
            ON CONFLICT (workspace_id, finding_id) DO NOTHING
        """),
        params,
    )
    await conn.execute(
        text("""
            WITH observations AS (
                SELECT occurred_at, payload->>'current_state' AS state,
                    lag(payload->>'current_state') OVER (
                        ORDER BY source_version ASC NULLS FIRST, occurred_at, event_id
                    ) AS previous_state
                FROM events
                WHERE workspace_id = :workspace_id AND finding_id = :finding_id
                    AND event_name = 'finding.observed'
            ), history AS (
                SELECT max(occurred_at) FILTER (WHERE state = 'resolved'
                        AND previous_state <> state) AS resolved_at,
                    max(occurred_at) FILTER (WHERE state = 'dismissed'
                        AND previous_state <> state) AS dismissed_at,
                    min(occurred_at) FILTER (WHERE state = 'resolved') AS first_resolved_at,
                    min(occurred_at) FILTER (WHERE state = 'dismissed') AS first_dismissed_at,
                    count(*) FILTER (WHERE state = 'open'
                        AND previous_state IN ('resolved', 'dismissed')) AS reopened_count
                FROM observations
            ), candidate AS (
                SELECT u.*, f.latest_occurred_at IS NULL OR
                    CASE WHEN u.source_version IS NOT NULL AND f.source_version IS NOT NULL
                        THEN (u.source_version, u.observed_at) >= (f.source_version, f.latest_occurred_at)
                        ELSE u.observed_at >= f.latest_occurred_at END AS replace_state
                FROM finding_usage_projection u JOIN finding_projection f
                    USING (workspace_id, finding_id)
                WHERE u.workspace_id = :workspace_id AND u.finding_id = :finding_id
            ), next AS (
                SELECT c.*,
                    GREATEST(h.resolved_at, f.resolved_at, f.history_baseline_resolved_at,
                        CASE WHEN f.resolved_at IS NULL THEN h.first_resolved_at END,
                        CASE WHEN c.replace_state AND c.current_state = 'resolved'
                            AND f.current_state <> 'resolved' THEN c.observed_at END
                    ) AS latest_resolved_at,
                    GREATEST(h.dismissed_at, f.dismissed_at, f.history_baseline_dismissed_at,
                        CASE WHEN f.dismissed_at IS NULL THEN h.first_dismissed_at END,
                        CASE WHEN c.replace_state AND c.current_state = 'dismissed'
                            AND f.current_state <> 'dismissed' THEN c.observed_at END
                    ) AS latest_dismissed_at,
                    GREATEST(h.reopened_count,
                        GREATEST(f.reopened_count, f.history_baseline_reopened_count) +
                        CASE WHEN c.replace_state AND c.current_state = 'open'
                            AND f.current_state IN ('resolved', 'dismissed') THEN 1 ELSE 0 END
                    ) AS total_reopened
                FROM candidate c CROSS JOIN history h JOIN finding_projection f
                    ON f.workspace_id = c.workspace_id AND f.finding_id = c.finding_id
            )
            UPDATE finding_projection f SET
                surfaced_at = LEAST(f.surfaced_at, n.surfaced_at),
                current_state = CASE WHEN n.replace_state THEN n.current_state ELSE f.current_state END,
                severity = CASE WHEN n.replace_state THEN n.severity ELSE f.severity END,
                category = CASE WHEN n.replace_state THEN n.category ELSE f.category END,
                source_version = CASE WHEN n.replace_state THEN n.source_version ELSE f.source_version END,
                latest_occurred_at = CASE WHEN n.replace_state THEN n.observed_at ELSE f.latest_occurred_at END,
                resolved_at = n.latest_resolved_at, dismissed_at = n.latest_dismissed_at,
                reopened_count = n.total_reopened,
                history_baseline_resolved_at = n.latest_resolved_at,
                history_baseline_dismissed_at = n.latest_dismissed_at,
                history_baseline_reopened_count = n.total_reopened,
                updated_at = clock_timestamp()
            FROM next n WHERE f.workspace_id = n.workspace_id AND f.finding_id = n.finding_id
        """),
        params,
    )
