"""Admin gate for tools wired only into admin threads.

Tools re-check the triggering user against ``CONFIGURED_ADMINS`` so a thread whose
metadata says "admin" cannot act on behalf of someone who is not one.
"""

from typing import Any

from langgraph.config import get_config

from ..settings.admin import is_admin


def configurable() -> dict[str, Any]:
    try:
        config = get_config()
    except Exception:
        return {}
    values = config.get("configurable") if isinstance(config, dict) else None
    return values if isinstance(values, dict) else {}


def require_admin(action: str) -> str | None:
    """Return an error message when the triggering user is not an admin."""
    values = configurable()
    login = values.get("github_login")
    email = values.get("user_email")
    if is_admin(
        email if isinstance(email, str) else None,
        login=login if isinstance(login, str) else None,
    ):
        return None
    return f"Only workspace admins can {action}."
