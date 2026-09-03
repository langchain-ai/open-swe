"""Tool: persist the triggering user's user-level custom instructions."""

import logging
from typing import Any

from langgraph.config import get_config

from agent.dashboard.agent_overrides import resolve_github_login
from agent.dashboard.user_instructions import MAX_USER_INSTRUCTIONS_CHARS, set_user_instructions
from agent.utils.json_types import as_json_object

logger = logging.getLogger(__name__)


async def save_user_instructions(instructions: str) -> dict[str, Any]:
    """Save the triggering user's standing, user-level custom instructions.

    Call this only when the user explicitly says a standing behavioural preference
    ("always …", "never …", "from now on …", "stop doing …") is personal to
    them and should apply to all their future runs. If personal versus shared/global
    scope is unclear, ask the user which scope they intend before calling this tool.

    This is not general-purpose memory. Do not use it for one-off task details,
    conversation context, shared team/repository/project facts, or preferences
    concerning other users.

    This is a full replacement: pass the COMPLETE new instruction text. Your
    current user-level instructions are shown in your system prompt under "Your
    Custom Instructions (user-level)"; preserve those lines and add the new rule
    unless the user asked you to change or remove something. Pass an empty string
    only when the user asks to clear their instructions.

    The user can also edit them in the dashboard Profile tab.

    A thread's system prompt is fixed when the thread opens, so it keeps showing
    the old text after this call. The ``reminder`` in the result is the current
    version — follow it for the rest of the thread.

    Args:
        instructions: The complete user-level instruction text (markdown).

    Returns:
        ``{"ok": True, "login": str, "instructions": str, "reminder": str}`` on
        success, or ``{"ok": False, "error": str}`` when the user could not be
        resolved.
    """
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
