"""Tools for managing the triggering user's skills."""

from typing import Any

from langgraph.config import get_config

from agent.dashboard.agent_overrides import resolve_github_login
from agent.skill_store.store import (
    SkillCreate,
    SkillUpdate,
    create_skill,
    delete_skill,
    get_skill,
    update_skill,
)
from agent.tools.access import Policy, access, unchanged
from agent.utils.json_types import as_json_object

_OWN = Policy(trusted="private", actor="owner", sole=unchanged)


async def _login() -> str | None:
    return await resolve_github_login(as_json_object(get_config()))


@access(_OWN)
async def save_user_skill(name: str, description: str, instructions: str = "") -> dict[str, Any]:
    """Implement the `save_user_skill` tool."""
    login = await _login()
    if not login:
        return {"ok": False, "error": "Could not resolve the triggering user's GitHub login"}

    existing = await get_skill(login, name)
    if existing:
        skill = await update_skill(
            login, name, SkillUpdate(description=description, instructions=instructions)
        )
    else:
        skill = await create_skill(
            login, SkillCreate(name=name, description=description, instructions=instructions)
        )
    return {"ok": True, "skill": skill}


@access(_OWN)
async def delete_user_skill(name: str) -> dict[str, Any]:
    """Implement the `delete_user_skill` tool."""
    login = await _login()
    if not login:
        return {"ok": False, "error": "Could not resolve the triggering user's GitHub login"}

    await delete_skill(login, name)
    return {"ok": True, "name": name}
