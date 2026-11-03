"""Authenticated personal chat project API."""

from uuid import UUID

from fastapi import APIRouter, Query, Response

from openswe.audit_logs.middleware import audit_endpoint
from openswe.chat_projects.models import (
    ChatProject,
    ProjectBody,
    ProjectCollection,
    ProjectMembershipBody,
    ProjectThreadsPage,
)
from openswe.chat_projects.store import ChatProjects
from openswe.dashboard.deps import SESSION_DEP

router = APIRouter(prefix="/chat-projects", tags=["chat-projects"])


@router.get("")
async def list_projects(session: dict[str, str] = SESSION_DEP) -> ProjectCollection:
    return await (await ChatProjects.for_session(session)).collection()


@router.post("", status_code=201)
@audit_endpoint
async def create_project(body: ProjectBody, session: dict[str, str] = SESSION_DEP) -> ChatProject:
    return await (await ChatProjects.for_session(session)).create(body)


@router.patch("/{project_id}")
@audit_endpoint
async def update_project(
    project_id: UUID, body: ProjectBody, session: dict[str, str] = SESSION_DEP
) -> ChatProject:
    return await (await ChatProjects.for_session(session)).update(project_id, body)


@router.delete("/{project_id}", status_code=204)
@audit_endpoint
async def delete_project(project_id: UUID, session: dict[str, str] = SESSION_DEP) -> Response:
    await (await ChatProjects.for_session(session)).delete(project_id)
    return Response(status_code=204)


@router.put("/threads/{thread_id}", status_code=204)
@audit_endpoint
async def assign_project(
    thread_id: str, body: ProjectMembershipBody, session: dict[str, str] = SESSION_DEP
) -> Response:
    await (await ChatProjects.for_session(session)).assign(thread_id, body.projectId)
    return Response(status_code=204)


@router.get("/{project_id}/threads")
async def project_threads(
    project_id: UUID,
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    resolved: bool = False,
    session: dict[str, str] = SESSION_DEP,
) -> ProjectThreadsPage:
    return await (await ChatProjects.for_session(session)).threads(
        project_id, limit=limit, offset=offset, resolved=resolved
    )
