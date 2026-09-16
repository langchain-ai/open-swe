"""Admin gate for tools wired only into admin threads.

Tools recheck user admin membership or a system invocation's saved authorization.
"""

from agent.dashboard.admin import is_admin
from agent.dashboard.user_mappings import email_for_login
from agent.run_config import RunConfig
from agent.schedules.store import authorized_admin_schedule


def configurable() -> RunConfig:
    try:
        return RunConfig.from_runtime()
    except Exception:
        return RunConfig()


async def actor_is_admin(cfg: RunConfig, *, login: str | None = None) -> bool:
    """Whether the run's actor is a configured admin, or a scheduled run carries one's authorization."""
    if cfg.source == "schedule":
        return await authorized_admin_schedule(cfg) is not None
    login = login or cfg.github_login
    if is_admin(cfg.user_email, login=login):
        return True
    return is_admin(await email_for_login(login), login=login)


async def is_private_admin_thread(cfg: RunConfig, *, login: str | None = None) -> bool:
    """Whether this run is a private admin thread whose actor is still an admin.

    The dashboard only stamps ``admin_thread`` for an admin session, but the flag
    is re-checked against ``CONFIGURED_ADMINS`` so a thread cannot carry the
    capability to a non-admin who later messages it.
    """
    return cfg.admin_thread is True and await actor_is_admin(cfg, login=login)


async def require_admin(action: str) -> str | None:
    """Recheck either the triggering admin or the saved system authorization."""
    if await actor_is_admin(configurable()):
        return None
    return f"Only workspace admins can {action}."


async def require_private_admin_thread(action: str) -> str | None:
    if await is_private_admin_thread(configurable()):
        return None
    return f"Only workspace admins in a private admin thread can {action}."
