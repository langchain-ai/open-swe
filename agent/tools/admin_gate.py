"""Admin gate for tools wired only into admin threads.

Tools recheck user admin membership or a system invocation's saved authorization.
"""

from agent.dashboard.admin import is_admin
from agent.dashboard.user_mappings import email_for_login
from agent.run_config import RunConfig
from agent.schedules.store import authorized_admin_schedule
from agent.slack.dm import is_dm_session


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


def is_private_admin_surface(cfg: RunConfig) -> bool:
    """Whether this run comes from a private surface stamped for admin use."""
    dashboard = cfg.source in {None, "dashboard"}
    slack_dm = (
        cfg.source == "slack"
        and cfg.slack_thread is not None
        and is_dm_session(cfg.slack_thread.channel_context, cfg.slack_thread.thread_ts)
    )
    return cfg.admin_thread is True and (dashboard or slack_dm)


async def actor_has_admin_context(cfg: RunConfig, *, login: str | None = None) -> bool:
    """Whether an admin-stamped run's current actor remains authorized."""
    return cfg.admin_thread is True and await actor_is_admin(cfg, login=login)


async def actor_has_private_admin_surface(cfg: RunConfig, *, login: str | None = None) -> bool:
    """Whether a private admin surface's current actor remains authorized."""
    return is_private_admin_surface(cfg) and await actor_is_admin(cfg, login=login)


async def require_admin(action: str) -> str | None:
    """Recheck either the triggering admin or the saved system authorization."""
    if await actor_is_admin(configurable()):
        return None
    return f"Only workspace admins can {action}."


async def require_private_admin_surface(action: str) -> str | None:
    if await actor_has_private_admin_surface(configurable()):
        return None
    return f"Only workspace admins on a private admin surface can {action}."
