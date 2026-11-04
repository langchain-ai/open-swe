"""Dashboard API for projects: each person's own, and the ones shared with them."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from openswe.audit_logs.middleware import audit_endpoint
from openswe.dashboard.deps import SESSION_DEP
from openswe.dashboard.principal import DashboardPrincipal
from openswe.projects.store import (
    NAME_MAX_CHARS,
    PROJECTS,
    Project,
    ProjectChanges,
    ProjectError,
    ProjectRole,
)
from openswe.users import User

router = APIRouter(tags=["projects"])


async def require_viewer(session: dict[str, object] = SESSION_DEP) -> User:
    user = await DashboardPrincipal.from_session(session).user()
    if user is None:
        raise HTTPException(401, "sign in again to use projects")
    return user


Viewer = Annotated[User, Depends(require_viewer)]


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=NAME_MAX_CHARS)
    workspace: str | None = None
    repo_full_name: str | None = None


class MemberSet(BaseModel):
    # A GitHub login or an email address, resolved to the person here.
    person: str = Field(min_length=1)
    role: ProjectRole = "member"


@router.get("/projects")
async def api_list_projects(viewer: Viewer) -> dict[str, list[Project]]:
    return {"projects": await PROJECTS.for_member(viewer)}


@router.post("/projects")
@audit_endpoint
async def api_create_project(body: ProjectCreate, request: Request, viewer: Viewer) -> Project:
    try:
        return await PROJECTS.create(
            viewer, body.name, workspace=body.workspace, repo_full_name=body.repo_full_name
        )
    except ProjectError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc


@router.patch("/projects/{project_id}")
@audit_endpoint
async def api_update_project(
    project_id: UUID, body: ProjectChanges, request: Request, viewer: Viewer
) -> Project:
    try:
        return await PROJECTS.update(project_id, viewer, body)
    except ProjectError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc


@router.delete("/projects/{project_id}")
@audit_endpoint
async def api_delete_project(project_id: UUID, request: Request, viewer: Viewer) -> Response:
    try:
        await PROJECTS.delete(project_id, viewer)
    except ProjectError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc
    return Response(status_code=204)


@router.put("/projects/{project_id}/members")
@audit_endpoint
async def api_set_project_member(
    project_id: UUID, body: MemberSet, request: Request, viewer: Viewer
) -> Project:
    member = await User.for_handle(body.person)
    if member is None:
        raise HTTPException(404, f"no Open SWE user {body.person!r}")
    try:
        return await PROJECTS.set_member(project_id, viewer, member, body.role)
    except ProjectError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc


@router.delete("/projects/{project_id}/members/{user_id}")
@audit_endpoint
async def api_remove_project_member(
    project_id: UUID, user_id: UUID, request: Request, viewer: Viewer
) -> Response:
    member = viewer if user_id == viewer.id else await User.get(user_id)
    if member is None:
        raise HTTPException(404, "member not found")
    try:
        await PROJECTS.remove_member(project_id, viewer, member)
    except ProjectError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc
    return Response(status_code=204)


@router.put("/projects/{project_id}/threads/{thread_id}")
@audit_endpoint
async def api_file_thread(
    project_id: UUID, thread_id: str, request: Request, viewer: Viewer
) -> Response:
    try:
        await PROJECTS.file_thread(thread_id, viewer, project_id)
    except ProjectError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc
    return Response(status_code=204)


@router.delete("/projects/threads/{thread_id}")
@audit_endpoint
async def api_unfile_thread(thread_id: str, request: Request, viewer: Viewer) -> Response:
    try:
        await PROJECTS.file_thread(thread_id, viewer, None)
    except ProjectError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc
    return Response(status_code=204)
