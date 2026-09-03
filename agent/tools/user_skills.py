"""Tools for managing the triggering user's skills."""

from typing import Any

from langgraph.config import get_config

from agent.dashboard.agent_overrides import resolve_github_login
from agent.dashboard.skills import (
    SkillCreate,
    SkillUpdate,
    create_skill,
    delete_skill,
    get_skill,
    update_skill,
)
from agent.utils.json_types import as_json_object


def _login() -> str | None:
    return resolve_github_login(as_json_object(get_config()))


async def save_user_skill(name: str, description: str, instructions: str = "") -> dict[str, Any]:
    """Create or update a skill owned by the triggering user.

    Call this when the user asks to create or modify a reusable skill. The name
    must use lowercase letters, numbers, and single hyphens. Changes apply to
    future runs and cannot affect another user's or a bundled skill.
    """
    login = _login()
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


async def delete_user_skill(name: str) -> dict[str, Any]:
    """Delete a skill owned by the triggering user when they explicitly request it."""
    login = _login()
    if not login:
        return {"ok": False, "error": "Could not resolve the triggering user's GitHub login"}

    await delete_skill(login, name)
    return {"ok": True, "name": name}
