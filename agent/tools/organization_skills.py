"""Admin-thread tools for managing organization-wide skills."""

from typing import Any

from fastapi import HTTPException

from agent.skill_store import store
from agent.tools.access import Policy, access, ack

_WRITE = Policy(trusted="admin_thread", actor="admin", sole=ack("name", "skill.name"))


@access(_WRITE)
async def save_organization_skill(
    name: str, description: str, instructions: str = ""
) -> dict[str, Any]:
    """Implement the `save_organization_skill` tool."""
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


@access(_WRITE)
async def delete_organization_skill(name: str) -> dict[str, Any]:
    """Implement the `delete_organization_skill` tool."""
    try:
        await store.delete_organization_skill(name)
    except HTTPException as exc:
        return {"ok": False, "error": str(exc.detail)}
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "name": name}
