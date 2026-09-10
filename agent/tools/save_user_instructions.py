"""Tool: persist the triggering user's user-level custom instructions."""

import logging
from typing import Any

from langgraph.config import get_config

from agent.dashboard.agent_overrides import resolve_github_login
from agent.dashboard.user_instructions import MAX_USER_INSTRUCTIONS_CHARS, set_user_instructions
from agent.utils.json_types import as_json_object

logger = logging.getLogger(__name__)


async def save_user_instructions(instructions: str) -> dict[str, Any]:
    """Implement the `save_user_instructions` tool."""
    login = resolve_github_login(as_json_object(get_config()))
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
    except Exception as exc:
        logger.exception("Failed to save user instructions for %s", login)
        return {"ok": False, "error": f"failed to save user instructions: {exc}"}

    saved = record.get("instructions", text)
    return {
        "ok": True,
        "login": login,
        "instructions": saved,
        "reminder": (
            "<system-reminder>\n"
            f"@{login}'s user-level custom instructions were just replaced with the text "
            'below. The copy under "Your Custom Instructions (user-level)" in your system '
            "prompt is the version this thread opened with and is now stale; follow this "
            "text instead for the rest of the thread.\n\n"
            f"{saved or '(cleared — they now have no user-level instructions)'}\n"
            "</system-reminder>"
        ),
    }
