"""Responder-only access to incident summaries and retained incident history."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query

from agent.incidents import documents
from agent.incidents import routes as incidents_routes

router = APIRouter(tags=["incidents"])
Responder = Annotated[dict[str, Any], Depends(incidents_routes.responder)]


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
