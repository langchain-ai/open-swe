"""Shared short-TTL cache of global Robin defaults."""

import logging
from collections.abc import Awaitable, Callable
from datetime import timedelta
from uuid import uuid4

from openswe.dashboard.settings import (
    Settings,
    fallback_settings,
    load_instance_settings,
)

logger = logging.getLogger(__name__)

SETTINGS_TTL = timedelta(seconds=60)
_GENERATION_KEY = "openswe:settings:generation"


async def _cached(name: str, load: Callable[[], Awaitable[Settings]]) -> Settings:
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
        return Settings(cached)
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


async def cached_settings() -> Settings:
    """The global defaults used by every run."""
    return await cached_instance_settings()


async def cached_instance_settings() -> Settings:
    """The cached global defaults."""
    return await _cached("instance", load_instance_settings)


async def forget_cached_settings() -> None:
    """Invalidate cached defaults on every replica after a save."""
    from langgraph_api.cache import cache_set

    try:
        await cache_set(_GENERATION_KEY, uuid4().hex)
    except Exception:
        logger.warning("Could not clear the shared settings cache", exc_info=True)
