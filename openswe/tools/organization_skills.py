"""Admin-thread tools for managing organization-wide skills."""

from fastapi import HTTPException
from pydantic import JsonValue

from openswe.audit_logs.tools import audit_tool
from openswe.skill_store import store
from openswe.tools.access import Policy, access, ack
from openswe.tools.mcp_exposure import expose_mcp

_WRITE = Policy(trusted="admin_thread", actor="admin", sole=ack("name", "skill.name"))


@expose_mcp(access="admin")
@audit_tool()
@access(_WRITE)
async def save_organization_skill(
    name: str, description: str, instructions: str = ""
) -> dict[str, JsonValue]:
    """Implement the `save_organization_skill` tool."""
    try:
        body = store.SkillCreate(name=name, description=description, instructions=instructions)
        async with store.HubSkills.open() as skills:
            created = await skills.get(body.name) is None
            skill = await skills.save(body, create=created)
    except HTTPException as exc:
        return {"ok": False, "error": str(exc.detail)}
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "skill": skill, "created": created}


@expose_mcp(access="admin")
@audit_tool()
@access(_WRITE)
async def delete_organization_skill(name: str) -> dict[str, JsonValue]:
    """Implement the `delete_organization_skill` tool."""
    try:
        async with store.HubSkills.open() as skills:
            await skills.delete(name)
    except HTTPException as exc:
        return {"ok": False, "error": str(exc.detail)}
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "name": name}
