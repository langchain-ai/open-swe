"""Durable authoritative PR revision endpoints and distance recovery."""

import logging
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID, uuid5

from pydantic import BaseModel, ConfigDict
from sqlalchemy import text

from agent import database
from agent.analytics import distance, emitter
from agent.analytics.events import GitCommitSHA, PRDistanceMeasuredPayload
from agent.database import analytics as analytics_db

EndpointKind = Literal["opening", "final"]
SourceKind = Literal["creation_response", "webhook"]
_NAMESPACE = UUID("729b0453-bbc5-5a0f-9e49-870fe7cfe97d")
logger = logging.getLogger(__name__)


class PRRevisionConflictError(ValueError):
    """New endpoint evidence conflicts with the authoritative retained snapshot."""


class PRRevisionEvidence(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    repository_full_name: str
    pr_number: int
    endpoint_kind: EndpointKind
    base_sha: GitCommitSHA
    head_sha: GitCommitSHA
    endpoint_at: datetime
    captured_at: datetime
    source_kind: SourceKind
    source_id: str | None


def _evidence_id(
    workspace_id: UUID, repository: str, number: int, endpoint_kind: EndpointKind
) -> UUID:
    return uuid5(_NAMESPACE, f"{workspace_id}:{repository}:{number}:{endpoint_kind}")


async def capture_pr_revision(
    *,
    owner: str,
    repo: str,
    number: int,
    endpoint_kind: EndpointKind,
    base_sha: str,
    head_sha: str,
    endpoint_at: datetime,
    source_kind: SourceKind,
    source_id: str | None = None,
) -> PRRevisionEvidence:
    repository = f"{owner.lower()}/{repo.lower()}"
    workspace_id = analytics_db.workspace_id()
    evidence = PRRevisionEvidence.model_validate(
        {
            "id": _evidence_id(workspace_id, repository, number, endpoint_kind),
            "repository_full_name": repository,
            "pr_number": number,
            "endpoint_kind": endpoint_kind,
            "base_sha": base_sha,
            "head_sha": head_sha,
            "endpoint_at": endpoint_at,
            "captured_at": datetime.now(endpoint_at.tzinfo),
            "source_kind": source_kind,
            "source_id": source_id,
        }
    )
    params = {"workspace_id": workspace_id, **evidence.model_dump()}
    async with database.transaction() as conn:
        inserted = await conn.scalar(
            text(
                "INSERT INTO pr_revision_evidence (id, workspace_id, repository_full_name, "
                "pr_number, endpoint_kind, base_sha, head_sha, endpoint_at, captured_at, "
                "source_kind, source_id) VALUES (:id, :workspace_id, :repository_full_name, "
                ":pr_number, :endpoint_kind, :base_sha, :head_sha, :endpoint_at, :captured_at, "
                ":source_kind, :source_id) ON CONFLICT DO NOTHING RETURNING id"
            ),
            params,
        )
        if inserted is None:
            retained = (
                (
                    await conn.execute(
                        text(
                            "SELECT id, repository_full_name, pr_number, endpoint_kind, base_sha, "
                            "head_sha, endpoint_at, captured_at, source_kind, source_id "
                            "FROM pr_revision_evidence WHERE workspace_id = :workspace_id "
                            "AND repository_full_name = :repository_full_name AND pr_number = :pr_number "
                            "AND endpoint_kind = :endpoint_kind"
                        ),
                        params,
                    )
                )
                .mappings()
                .one()
            )
            if retained["base_sha"] != base_sha or retained["head_sha"] != head_sha:
                raise PRRevisionConflictError(
                    f"Conflicting {endpoint_kind} revision evidence for {repository}#{number}"
                )
            return PRRevisionEvidence.model_validate(retained)
    return evidence


async def retry_pr_distance(*, owner: str, repo: str, number: int) -> int | None:
    repository = f"{owner.lower()}/{repo.lower()}"
    async with database.connection() as conn:
        rows = (
            (
                await conn.execute(
                    text(
                        "SELECT id, endpoint_kind, base_sha, head_sha FROM pr_revision_evidence "
                        "WHERE workspace_id = :workspace_id AND repository_full_name = :repository "
                        "AND pr_number = :number"
                    ),
                    {
                        "workspace_id": analytics_db.workspace_id(),
                        "repository": repository,
                        "number": number,
                    },
                )
            )
            .mappings()
            .all()
        )
    endpoints = {row["endpoint_kind"]: row for row in rows}
    opening = endpoints.get("opening")
    final = endpoints.get("final")
    if opening is None or final is None:
        return None
    basis_points = await distance.post_open_distance_basis_points(
        owner=owner,
        repo=repo,
        opening_base_sha=opening["base_sha"],
        opening_head_sha=opening["head_sha"],
        final_base_sha=final["base_sha"],
        final_head_sha=final["head_sha"],
    )
    if basis_points is None:
        return None
    queued = await emitter.pr_distance_measured(
        PRDistanceMeasuredPayload(
            repository_full_name=repository,
            pr_number=number,
            distance_basis_points=basis_points,
            opening_base_sha=opening["base_sha"],
            opening_head_sha=opening["head_sha"],
            final_base_sha=final["base_sha"],
            final_head_sha=final["head_sha"],
            algorithm_revision="myers-line-v1",
            opening_evidence_ref=opening["id"],
            final_evidence_ref=final["id"],
        ),
        measured_at=datetime.now(UTC),
    )
    if not queued:
        logger.info(
            "PR distance measurement was already queued",
            extra={"repository": repository, "pr_number": number},
        )
    return basis_points
