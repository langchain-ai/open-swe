"""Per-repo instructions the coding agent is handed on every run."""

from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from ...settings.agent_instructions import (
    AgentInstructionsCreate,
    AgentInstructionsUpdate,
    create_agent_instructions,
    delete_agent_instructions,
    get_agent_instructions,
    list_agent_instructions,
    set_agent_instructions,
)
from ..authz import REPO_FULL_NAME_ACCESS, SESSION, RepoAccess
from ..repo_access import filter_repo_records_for_user, require_repo_access_for_user

router = APIRouter()


@router.get("/agent-instructions")
async def api_list_agent_instructions(
    session: dict[str, Any] = SESSION,
) -> list[dict[str, Any]]:
    return await filter_repo_records_for_user(session["sub"], await list_agent_instructions())


@router.post("/agent-instructions")
async def api_create_agent_instructions(
    body: AgentInstructionsCreate,
    session: dict[str, Any] = SESSION,
) -> dict[str, Any]:
    await require_repo_access_for_user(session["sub"], body.full_name)
    return await create_agent_instructions(body.full_name, session["sub"])


@router.get("/agent-instructions/{full_name:path}")
async def api_get_agent_instructions(
    access: RepoAccess = REPO_FULL_NAME_ACCESS,
) -> dict[str, Any]:
    record = await get_agent_instructions(access.full_name)
    if not record:
        raise HTTPException(404, "agent instructions not found")
    return record


@router.put("/agent-instructions/{full_name:path}")
async def api_update_agent_instructions(
    body: AgentInstructionsUpdate,
    access: RepoAccess = REPO_FULL_NAME_ACCESS,
) -> dict[str, Any]:
    return await set_agent_instructions(access.full_name, body.instructions)


@router.delete("/agent-instructions/{full_name:path}")
async def api_delete_agent_instructions(
    access: RepoAccess = REPO_FULL_NAME_ACCESS,
) -> Response:
    record = await get_agent_instructions(access.full_name)
    if not record:
        raise HTTPException(404, "agent instructions not found")
    await delete_agent_instructions(access.full_name)
    return Response(status_code=204)
