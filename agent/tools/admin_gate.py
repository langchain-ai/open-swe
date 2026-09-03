"""Admin gate for tools wired only into admin threads.

Tools re-check the triggering user against ``CONFIGURED_ADMINS`` so a thread whose
metadata says "admin" cannot act on behalf of someone who is not one.
"""

from langgraph.config import get_config

from agent.dashboard.admin import is_admin
from agent.run_config import RunConfig


def configurable() -> RunConfig:
    try:
        return RunConfig.from_config(get_config())
    except Exception:
        return RunConfig()


def require_admin(action: str) -> str | None:
    """Return an error message when the triggering user is not an admin."""
    cfg = configurable()
    if is_admin(cfg.user_email, login=cfg.github_login):
        return None
    return f"Only workspace admins can {action}."
