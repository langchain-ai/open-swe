"""Fine-grained personal chat project tools."""

from uuid import UUID

from fastapi import HTTPException
from langgraph.config import get_config

from openswe.audit_logs.tools import audit_tool
from openswe.chat_projects.models import LocalFolder, ProjectBody
from openswe.chat_projects.store import ChatProjects
from openswe.tools.access import Policy, access, ack
from openswe.tools.mcp_exposure import expose_mcp


@expose_mcp()
@access(Policy(trusted="private", actor="owner"))
async def list_chat_projects() -> dict[str, object]:
    """List the requester's personal projects and readable chat memberships."""
    try:
        projects = await (await ChatProjects.for_config(get_config())).collection()
        return {"ok": True, **projects.model_dump(mode="json")}
    except HTTPException as exc:
        return {"ok": False, "error": str(exc.detail)}


@expose_mcp()
@audit_tool()
@access(Policy(trusted="private", actor="owner", sole=ack(), direct=True))
async def create_chat_project(
    name: str, repo: str | None = None, local_folder: LocalFolder | None = None
) -> dict[str, object]:
    """Create a personal project; its optional context never grants access."""
    try:
        body = ProjectBody(name=name, repo=repo, localFolder=local_folder)
        project = await (await ChatProjects.for_config(get_config())).create(body)
        return {"ok": True, "project": project.model_dump(mode="json")}
    except (HTTPException, ValueError) as exc:
        return {
            "ok": False,
            "error": str(exc.detail) if isinstance(exc, HTTPException) else str(exc),
        }


@expose_mcp()
@audit_tool()
@access(Policy(trusted="private", actor="owner", sole=ack(), direct=True))
async def update_chat_project(
    project_id: str,
    name: str,
    repo: str | None = None,
    local_folder: LocalFolder | None = None,
) -> dict[str, object]:
    """Replace a personal project's name and optional repository or local context."""
    try:
        body = ProjectBody(name=name, repo=repo, localFolder=local_folder)
        project = await (await ChatProjects.for_config(get_config())).update(UUID(project_id), body)
        return {"ok": True, "project": project.model_dump(mode="json")}
    except (HTTPException, ValueError) as exc:
        return {
            "ok": False,
            "error": str(exc.detail) if isinstance(exc, HTTPException) else str(exc),
        }


@expose_mcp()
@audit_tool()
@access(Policy(trusted="private", actor="owner", sole=ack(), direct=True))
async def delete_chat_project(project_id: str) -> dict[str, object]:
    """Delete a personal project and its memberships, never its chats or files."""
    try:
        await (await ChatProjects.for_config(get_config())).delete(UUID(project_id))
        return {"ok": True}
    except (HTTPException, ValueError) as exc:
        return {
            "ok": False,
            "error": str(exc.detail) if isinstance(exc, HTTPException) else str(exc),
        }


@expose_mcp()
@audit_tool()
@access(Policy(trusted="private", actor="owner", sole=ack(), direct=True))
async def assign_chat_project(thread_id: str, project_id: str | None) -> dict[str, object]:
    """Move a readable chat into one personal project, or ungroup it with null."""
    try:
        await (await ChatProjects.for_config(get_config())).assign(
            thread_id, UUID(project_id) if project_id is not None else None
        )
        return {"ok": True}
    except (HTTPException, ValueError) as exc:
        return {
            "ok": False,
            "error": str(exc.detail) if isinstance(exc, HTTPException) else str(exc),
        }
