from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

_CACHE: dict[str, tuple[object, float]] = {}
_LOCKS: dict[tuple[str, int], asyncio.Lock] = {}


def _now() -> float:
    return asyncio.get_running_loop().time()


def _lock_for(key: str) -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    lock_key = (key, id(loop))
    lock = _LOCKS.get(lock_key)
    if lock is None:
        lock = asyncio.Lock()
        _LOCKS[lock_key] = lock
    return lock


async def cached(key: str, ttl_seconds: float, loader: Callable[[], Awaitable[T]]) -> T:
    now = _now()
    entry = _CACHE.get(key)
    if entry is not None:
        value, expires_at = entry
        if expires_at > now:
            return value  # type: ignore[return-value]

    async with _lock_for(key):
        now = _now()
        entry = _CACHE.get(key)
        if entry is not None:
            value, expires_at = entry
            if expires_at > now:
                return value  # type: ignore[return-value]

        stale = entry[0] if entry is not None else None
        has_stale = entry is not None
        try:
            value = await loader()
        except Exception:
            if has_stale:
                logger.warning(
                    "TTL cache loader failed for %s; serving stale value", key, exc_info=True
                )
                return stale  # type: ignore[return-value]
            raise
        _CACHE[key] = (value, now + ttl_seconds)
        return value


def set_cached(key: str, value: object, ttl_seconds: float) -> None:
    try:
        expires_at = _now() + ttl_seconds
    except RuntimeError:
        expires_at = float("inf")
    _CACHE[key] = (value, expires_at)


def invalidate(key: str) -> None:
    _CACHE.pop(key, None)


def clear() -> None:
    _CACHE.clear()
    _LOCKS.clear()
