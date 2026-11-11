"""Tool: persist the triggering user's user-level custom instructions."""

import logging

from langgraph.config import get_config

from openswe.audit_logs.tools import audit_tool
from openswe.dashboard.agent_overrides import resolve_github_login
from openswe.dashboard.user_instructions import MAX_USER_INSTRUCTIONS_CHARS, set_user_instructions
from openswe.prompts import prompt
from openswe.tools.access import Policy, access, unchanged
from openswe.tools.mcp_exposure import expose_mcp
from openswe.utils.json_types import as_json_object

logger = logging.getLogger(__name__)


@expose_mcp()
@audit_tool()
@access(Policy(trusted="private", actor="owner", sole=unchanged))
async def save_user_instructions(instructions: str) -> dict[str, str | bool]:
    """Implement the `save_user_instructions` tool."""
    login = await resolve_github_login(as_json_object(get_config()))
    if not login:
        return {
            "ok": False,
            "error": (
                "Could not resolve the triggering user's GitHub login, so there is no "
                "profile to save instructions to. Ask the user to set them in the "
                "dashboard Profile tab."
            ),
        }

    text = (instructions or "").strip()
    if len(text) > MAX_USER_INSTRUCTIONS_CHARS:
        return {
            "ok": False,
            "error": f"instructions exceed the {MAX_USER_INSTRUCTIONS_CHARS} character limit",
        }

    try:
        record = await set_user_instructions(login, text, updated_by="open-swe")
    except Exception:
        logger.exception("Failed to save user instructions", extra={"github_login": login})
        return {"ok": False, "error": "failed to save user instructions"}

    saved = record.get("instructions", text)
    return {
        "ok": True,
        "login": login,
        "instructions": saved,
        "reminder": prompt("runs/save-user-instructions-reminder", login=login, saved=saved),
    }
