"""Admin gate for tools wired only into admin threads.

Tools recheck user admin membership or a system invocation's saved authorization.
"""

from agent.dashboard.admin import is_admin
from agent.dashboard.schedules import authorized_admin_schedule
from agent.github.system_scope import system_repository_scope
from agent.run_config import RunConfig


def configurable() -> RunConfig:
    try:
        return RunConfig.from_runtime()
    except Exception:
        return RunConfig()


async def require_admin(action: str) -> str | None:
    """Recheck either the triggering admin or the saved system authorization."""
    cfg = configurable()
    if cfg.thread_id and await system_repository_scope(cfg.thread_id) is not None:
        return f"System threads cannot {action}."
    allowed = (
        await authorized_admin_schedule(cfg) is not None
        if cfg.source == "schedule"
        else is_admin(cfg.user_email, login=cfg.github_login)
    )
    if allowed:
        return None
    return f"Only workspace admins can {action}."
