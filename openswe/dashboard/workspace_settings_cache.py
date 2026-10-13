"""Short-TTL read of the settings one workspace resolves to, shared by every worker.

Graph factories read these on every run, so they are cached, in the platform
cache every replica shares (Redis on a deployment, memory under ``langgraph dev``).
The slug is part of the key: the workspace that populated a key first must not
answer for the others. Keys also carry a generation that every save replaces,
so a save reaches every replica's next read at once.

Callers pass the run's slug explicitly. A factory runs outside the graph's
context, where :func:`openswe.dashboard.workspace_settings.resolve_settings_workspace`
cannot see a ``configurable`` and would silently answer ``default``.
"""

import logging
from collections.abc import Awaitable, Callable
from datetime import timedelta
from uuid import uuid4

from openswe.dashboard.workspace_settings import (
    WorkspaceSettings,
    fallback_settings,
    load_instance_settings,
    load_workspace_settings,
    resolve_settings_workspace,
)

logger = logging.getLogger(__name__)

SETTINGS_TTL = timedelta(seconds=60)
_GENERATION_KEY = "openswe:settings:generation"


async def _cached(name: str, load: Callable[[], Awaitable[WorkspaceSettings]]) -> WorkspaceSettings:
    from langgraph_api.cache import cache_get, cache_set

    key: str | None = None
    try:
        generation = await cache_get(_GENERATION_KEY)
        key = f"openswe:settings:{generation if isinstance(generation, str) else '0'}:{name}"
        cached = await cache_get(key)
    except Exception:
        logger.warning("Shared settings cache read failed", exc_info=True)
        cached = None
    if isinstance(cached, dict):
        return WorkspaceSettings(cached)
    try:
        settings = await load()
    except Exception:
        # Fail soft for this read only; caching the defaults would hand them to every replica.
        logger.warning("settings lookup failed; using defaults", exc_info=True)
        return fallback_settings()
    if key is not None:
        try:
            await cache_set(key, dict(settings), SETTINGS_TTL)
        except Exception:
            logger.warning("Shared settings cache write failed", exc_info=True)
    return settings


async def cached_workspace_settings(workspace: str | None) -> WorkspaceSettings:
    slug = resolve_settings_workspace(workspace)
    return await _cached(f"workspace:{slug}", lambda: load_workspace_settings(slug))


async def cached_instance_settings() -> WorkspaceSettings:
    """The instance record alone, for settings no workspace may override."""
    return await _cached("instance", load_instance_settings)


async def forget_cached_settings() -> None:
    """Start a new generation after a save; workspaces inherit the instance, so all go."""
    from langgraph_api.cache import cache_set

    try:
        await cache_set(_GENERATION_KEY, uuid4().hex)
    except Exception:
        logger.warning("Could not clear the shared settings cache", exc_info=True)
