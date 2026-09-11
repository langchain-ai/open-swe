"""Responder-only document reads and queued edits."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field

from agent.dashboard import incidents_api
from agent.incidents import documents
from agent.incidents.document_models import DocumentKind

router = APIRouter(tags=["incidents"])
Responder = Annotated[dict[str, Any], Depends(incidents_api.responder)]


class DocumentEditBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    markdown: str = Field(max_length=200000)
    expected_revision: int = Field(ge=0, strict=True)
    request_id: str = Field(min_length=1, max_length=200)


@router.get("/history")
async def search_history(
    session: Responder,
    q: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    cursor: str | None = None,
) -> dict[str, Any]:
    return await documents.search_history(q=q, limit=limit, cursor=cursor)


@router.get("/{incident_id}")
async def get_document(incident_id: str, session: Responder) -> dict[str, Any]:
    return await documents.document_context(incident_id)


@router.get("/{incident_id}/revisions")
async def list_revisions(
    incident_id: str, session: Responder, kind: DocumentKind = "postmortem"
) -> dict[str, Any]:
    return await documents.list_revisions(incident_id, kind)


@router.get("/{incident_id}/revisions/{revision}")
async def get_revision(
    incident_id: str,
    revision: int,
    session: Responder,
    kind: DocumentKind = "postmortem",
) -> dict[str, Any]:
    return await documents.get_revision(incident_id, kind, revision)


@router.get("/{incident_id}/operations/{operation_id}")
async def get_operation(incident_id: str, operation_id: str, session: Responder) -> dict[str, Any]:
    return await documents.get_operation(incident_id, operation_id)


@router.put("/{incident_id}/{kind}", status_code=202)
async def edit_document(
    incident_id: str, kind: DocumentKind, body: DocumentEditBody, session: Responder
) -> dict[str, Any]:
    return await documents.submit_edit(
        incident_id,
        kind=kind,
        markdown=body.markdown,
        expected_revision=body.expected_revision,
        request_id=body.request_id,
        actor=incidents_api.actor(session),
    )
