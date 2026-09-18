"""Short-TTL reads of the settings a workspace or the instance resolves to.

Graph factories read these on every run, so they are cached. The cache lives in
a module-level dict shared by every run in the process, which is why the slug is
part of the key: one worker serves every workspace, and the workspace that
populated a key first must not answer for the others.

Callers pass the run's slug explicitly. A factory runs outside the graph's
context, where :func:`agent.dashboard.workspace_settings.resolve_settings_workspace`
cannot see a ``configurable`` and would silently answer ``default``.
"""

from agent.dashboard.workspace_settings import (
    WorkspaceSettings,
    get_instance_settings,
    get_workspace_settings,
    resolve_settings_workspace,
)
from agent.utils import ttl_cache

SETTINGS_TTL_SECONDS = 60


async def cached_workspace_settings(workspace: str | None) -> WorkspaceSettings:
    slug = resolve_settings_workspace(workspace)
    return await ttl_cache.cached(
        f"settings:{slug}",
        SETTINGS_TTL_SECONDS,
        lambda: get_workspace_settings(slug),
    )


async def cached_instance_settings() -> WorkspaceSettings:
    return await ttl_cache.cached(
        "settings:instance",
        SETTINGS_TTL_SECONDS,
        get_instance_settings,
    )


def invalidate_settings_cache() -> None:
    """Drop cached settings so the next read sees a just-saved instance or workspace record."""
    ttl_cache.clear()
