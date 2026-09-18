"""Durable verified PR measurements, independent of lifecycle ordering."""

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from agent.analytics.events import EventEnvelope, PRDistanceMeasuredPayload, subject_uuid


class PRDistanceConflictError(ValueError):
    """A verified measurement conflicts with retained evidence or event identity."""


async def retain_pr_distance(
    conn: AsyncConnection, event: EventEnvelope, *, new_event: bool
) -> bool:
    payload = event.payload
    if not isinstance(payload, PRDistanceMeasuredPayload):
        raise ValueError("Expected a verified PR distance payload")
    repository = payload.repository_full_name
    if (
        event.pr_id != subject_uuid(event.workspace_id, "pr", f"{repository}#{payload.pr_number}")
        or event.repository_id != subject_uuid(event.workspace_id, "repository", repository)
        or event.source_version is not None
    ):
        raise ValueError(
            "Measurement identity must match its PR without a lifecycle source version"
        )
    params = {
        "workspace_id": event.workspace_id,
        "pr_id": event.pr_id,
        "repository_id": event.repository_id,
        "measurement": payload.model_dump_json(),
        "event_id": event.event_id,
        "measured_at": event.occurred_at,
        "recorded_at": event.recorded_at,
    }
    if not new_event:
        owns_event = await conn.scalar(
            text(
                "SELECT event_id = :event_id FROM pr_distance_measurements "
                "WHERE workspace_id = :workspace_id AND pr_id = :pr_id"
            ),
            params,
        )
        if not owns_event:
            raise PRDistanceConflictError(
                "Measurement event identity is already claimed without matching retained evidence"
            )
    inserted = None
    if new_event:
        inserted = await conn.scalar(
            text(
                "INSERT INTO pr_distance_measurements (workspace_id, pr_id, repository_id, "
                "measurement, event_id, measured_at, recorded_at) VALUES "
                "(:workspace_id, :pr_id, :repository_id, CAST(:measurement AS jsonb), "
                ":event_id, :measured_at, :recorded_at) ON CONFLICT DO NOTHING RETURNING event_id"
            ),
            params,
        )
    matches = await conn.scalar(
        text(
            "SELECT measurement = CAST(:measurement AS jsonb) AND repository_id = :repository_id "
            "FROM pr_distance_measurements WHERE workspace_id = :workspace_id AND pr_id = :pr_id"
        ),
        params,
    )
    if not matches:
        raise PRDistanceConflictError(
            "Verified PR distance conflicts with the retained measurement"
        )
    return inserted is not None


async def restore_pr_distance(conn: AsyncConnection, *, workspace_id: UUID, pr_id: UUID) -> None:
    """Restore only the distance cache from durable evidence in the caller's transaction."""
    await conn.execute(
        text(
            "UPDATE pr_projection p SET distance_basis_points = CASE WHEN p.current_state = 'merged' "
            "THEN (m.measurement ->> 'distance_basis_points')::integer ELSE NULL END "
            "FROM pr_distance_measurements m WHERE p.workspace_id = :workspace_id "
            "AND p.pr_id = :pr_id AND m.workspace_id = p.workspace_id AND m.pr_id = p.pr_id "
            "AND m.repository_id = p.repository_id"
        ),
        {"workspace_id": workspace_id, "pr_id": pr_id},
    )
