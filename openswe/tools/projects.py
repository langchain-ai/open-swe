"""Tools for the requester's projects: the same reads and changes as the dashboard.

Deleting a project stays in the dashboard.
"""

from uuid import UUID

from langgraph.config import get_config
from pydantic import ValidationError

from openswe.audit_logs.tools import audit_tool
from openswe.projects.store import PROJECTS, ProjectChanges, ProjectError, ProjectRole
from openswe.tools.access import Policy, access, ack, unchanged
from openswe.tools.mcp_exposure import expose_mcp
from openswe.users import User
from openswe.utils.json_types import as_json_object

_READ = Policy(trusted="private", actor="owner", sole=unchanged)
_WRITE = Policy(trusted="private", actor="owner", sole=ack("project.id", "project.name"))


@expose_mcp()
@access(_READ)
async def list_projects() -> dict[str, object]:
    """Implement the `list_projects` tool."""
    try:
        user = await PROJECTS.member_for_run(as_json_object(get_config()))
    except ProjectError as exc:
        return {"ok": False, "error": str(exc)}
    projects = await PROJECTS.for_member(user)
    return {"ok": True, "projects": [project.model_dump(mode="json") for project in projects]}


@expose_mcp()
@audit_tool()
@access(_WRITE)
async def create_project(
    name: str, workspace: str | None = None, repo_full_name: str | None = None
) -> dict[str, object]:
    """Implement the `create_project` tool."""
    try:
        user = await PROJECTS.member_for_run(as_json_object(get_config()))
        project = await PROJECTS.create(
            user, name, workspace=workspace, repo_full_name=repo_full_name
        )
    except (ProjectError, ValidationError) as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "project": project.model_dump(mode="json")}


@expose_mcp()
@audit_tool()
@access(_WRITE)
async def update_project(project_id: UUID, changes: ProjectChanges) -> dict[str, object]:
    """Implement the `update_project` tool."""
    try:
        user = await PROJECTS.member_for_run(as_json_object(get_config()))
        project = await PROJECTS.update(project_id, user, changes)
    except ProjectError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "project": project.model_dump(mode="json")}


@expose_mcp()
@audit_tool()
@access(_WRITE)
async def share_project(
    project_id: UUID, person: str, role: ProjectRole = "member"
) -> dict[str, object]:
    """Implement the `share_project` tool."""
    try:
        user = await PROJECTS.member_for_run(as_json_object(get_config()))
        member = await User.for_handle(person)
        if member is None:
            return {"ok": False, "error": f"no Open SWE user {person!r}"}
        project = await PROJECTS.set_member(project_id, user, member, role)
    except ProjectError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "project": project.model_dump(mode="json")}


@expose_mcp()
@audit_tool()
@access(_WRITE)
async def unshare_project(project_id: UUID, person: str) -> dict[str, object]:
    """Implement the `unshare_project` tool."""
    try:
        user = await PROJECTS.member_for_run(as_json_object(get_config()))
        member = await User.for_handle(person)
        if member is None:
            return {"ok": False, "error": f"no Open SWE user {person!r}"}
        await PROJECTS.remove_member(project_id, user, member)
    except ProjectError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True}


@expose_mcp()
@audit_tool()
@access(_WRITE)
async def move_thread_to_project(thread_id: str, project_id: UUID | None) -> dict[str, object]:
    """Implement the `move_thread_to_project` tool."""
    try:
        user = await PROJECTS.member_for_run(as_json_object(get_config()))
        await PROJECTS.file_thread(thread_id, user, project_id)
    except ProjectError as exc:
        return {"ok": False, "error": str(exc)}
    return {
        "ok": True,
        "thread_id": thread_id,
        "project_id": str(project_id) if project_id else None,
    }
