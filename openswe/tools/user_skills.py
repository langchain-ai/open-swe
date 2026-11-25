"""Tools for managing the triggering user's skills."""

from langgraph.config import get_config
from pydantic import JsonValue

from openswe.audit_logs.tools import audit_tool
from openswe.dashboard.agent_overrides import resolve_github_login
from openswe.skill_store.store import HubSkills, SkillCreate, delete_skill
from openswe.tools.access import Policy, access, unchanged
from openswe.tools.mcp_exposure import expose_mcp
from openswe.utils.json_types import as_json_object

_OWN = Policy(trusted="private", actor="owner", sole=unchanged)


async def _login() -> str | None:
    return await resolve_github_login(as_json_object(get_config()))


@expose_mcp()
@audit_tool()
@access(_OWN)
async def save_user_skill(
    name: str, description: str, instructions: str = ""
) -> dict[str, JsonValue]:
    """Implement the `save_user_skill` tool."""
    login = await _login()
    if not login:
        return {"ok": False, "error": "Could not resolve the triggering user's GitHub login"}

    async with HubSkills.open(await HubSkills.person(login)) as skills:
        body = SkillCreate(name=name, description=description, instructions=instructions)
        skill = await skills.save(body, create=await skills.get(body.name) is None)
    return {"ok": True, "skill": skill}


@expose_mcp()
@audit_tool()
@access(_OWN)
async def delete_user_skill(name: str) -> dict[str, JsonValue]:
    """Implement the `delete_user_skill` tool."""
    login = await _login()
    if not login:
        return {"ok": False, "error": "Could not resolve the triggering user's GitHub login"}

    await delete_skill(login, name)
    return {"ok": True, "name": name}
