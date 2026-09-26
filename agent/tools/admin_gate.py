"""Admin identity checks shared by tool access policies."""

from agent.dashboard.admin import is_admin
from agent.run_config import RunConfig
from agent.schedules.store import authorized_admin_schedule
from agent.slack.dm import is_dm_channel
from agent.users import User


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
    return is_admin(await User.email_for_login(login), login=login)


async def participant_is_admin(login: str) -> bool:
    """Whether this person is a configured admin, judged on their own identity alone.

    Unlike :func:`actor_is_admin`, the current run's actor plays no part: a
    roster describes each participant, so an admin requester must not make
    everyone else in the thread look like one.
    """
    return is_admin(await User.email_for_login(login), login=login)


def is_private_admin_surface(cfg: RunConfig) -> bool:
    """Whether this run comes from a private surface stamped for admin use."""
    dashboard = cfg.source in {None, "dashboard"}
    slack_dm = (
        cfg.source == "slack"
        and cfg.slack_thread is not None
        and is_dm_channel(cfg.slack_thread.channel_context)
    )
    return cfg.admin_thread is True and (dashboard or slack_dm)


async def actor_has_admin_context(cfg: RunConfig, *, login: str | None = None) -> bool:
    """Whether an admin-stamped run's current actor remains authorized."""
    return cfg.admin_thread is True and await actor_is_admin(cfg, login=login)


async def require_admin(action: str) -> str | None:
    """Recheck either the triggering admin or the saved system authorization."""
    if await actor_is_admin(configurable()):
        return None
    return f"Only workspace admins can {action}."
