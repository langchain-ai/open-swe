"""Admin-thread tools for managing organization-wide skills."""

from typing import Any

from fastapi import HTTPException

from agent.dashboard import skills as store
from agent.tools.admin_gate import require_admin

_ACTION = "manage organization skills"


async def save_organization_skill(
    name: str, description: str, instructions: str = ""
) -> dict[str, Any]:
    """Implement the `save_organization_skill` tool."""
    if error := require_admin(_ACTION):
        return {"ok": False, "error": error}
    try:
        body = store.SkillCreate(name=name, description=description, instructions=instructions)
        update = store.SkillUpdate(description=body.description, instructions=body.instructions)
        try:
            skill = await store.update_organization_skill(body.name, update)
            created = False
        except HTTPException as exc:
            if exc.status_code != 404:
                raise
            skill = await store.create_organization_skill(body)
            created = True
    except HTTPException as exc:
        return {"ok": False, "error": str(exc.detail)}
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "skill": skill, "created": created}


async def delete_organization_skill(name: str) -> dict[str, Any]:
    """Implement the `delete_organization_skill` tool."""
    if error := require_admin(_ACTION):
        return {"ok": False, "error": error}
    try:
        await store.delete_organization_skill(name)
    except HTTPException as exc:
        return {"ok": False, "error": str(exc.detail)}
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "name": name}
