"""Typed, bounded stale-while-revalidate over Agent Server's shared cache."""

import asyncio
import hashlib
import json
import logging
import time
from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import cast

from pydantic import BaseModel, JsonValue, TypeAdapter

logger = logging.getLogger(__name__)
_MAX_AGE_SECONDS = 86400
_CACHE_TIMEOUT_SECONDS = 1.0
_REFRESHES: dict[tuple[str, int], asyncio.Task[object]] = {}


class _Entry(BaseModel):
    value: JsonValue
    fresh_until: float
    expires_at: float


def scoped_key(family: str, *parts: str) -> str:
    digest = hashlib.sha256(json.dumps(parts).encode()).hexdigest()
    return f"{family}:{digest}"


async def _get(key: str) -> object:
    from langgraph_sdk.cache import cache_get

    async with asyncio.timeout(_CACHE_TIMEOUT_SECONDS):
        return await cache_get(key)


async def _set(key: str, value: JsonValue, ttl: timedelta) -> None:
    from langgraph_sdk.cache import cache_set

    async with asyncio.timeout(_CACHE_TIMEOUT_SECONDS):
        await cache_set(key, value, ttl=ttl)


async def _read(key: str) -> _Entry | None:
    try:
        raw = await _get(f"open-swe:metadata:v1:{key}")
        return _Entry.model_validate(raw) if raw is not None else None
    except Exception as exc:
        logger.warning("Shared cache read unavailable", extra={"cache_error": type(exc).__name__})
        return None


async def set_cached[T](
    key: str,
    value: T,
    ttl_seconds: float,
    *,
    adapter: TypeAdapter[T],
    max_age: float | None = None,
) -> None:
    lifetime = min(max_age if max_age is not None else ttl_seconds, _MAX_AGE_SECONDS)
    if not 0 <= ttl_seconds <= lifetime or lifetime <= 0:
        raise ValueError("Cache freshness must be between zero and a positive max age")
    now = time.time()
    entry = _Entry(
        value=adapter.dump_python(value, mode="json"),
        fresh_until=now + ttl_seconds,
        expires_at=now + lifetime,
    )
    try:
        await _set(
            f"open-swe:metadata:v1:{key}",
            entry.model_dump(mode="json"),
            ttl=timedelta(seconds=lifetime),
        )
    except Exception as exc:
        logger.warning("Shared cache write unavailable", extra={"cache_error": type(exc).__name__})


async def get_cached[T](key: str, *, adapter: TypeAdapter[T]) -> T | None:
    entry = await _read(key)
    if entry is None or time.time() >= entry.expires_at:
        return None
    try:
        return _decode(entry, adapter)
    except ValueError as exc:
        logger.warning("Shared cache value invalid", extra={"cache_error": type(exc).__name__})
        return None


def _decode[T](entry: _Entry, adapter: TypeAdapter[T]) -> T:
    return adapter.validate_python(entry.value)


async def cached[T](
    key: str,
    ttl_seconds: float,
    loader: Callable[[], Awaitable[T]],
    *,
    adapter: TypeAdapter[T],
    max_age: float | None = None,
    freshness: Callable[[T], float] | None = None,
) -> T:
    """Fail open on cache I/O, never on loader failures or hard expiry."""
    lifetime = min(max_age if max_age is not None else ttl_seconds, _MAX_AGE_SECONDS)
    if not 0 <= ttl_seconds <= lifetime or lifetime <= 0:
        raise ValueError("Cache freshness must be between zero and a positive max age")
    entry = await _read(key)
    value: T | None = None
    if entry is not None and time.time() < entry.expires_at:
        try:
            value = _decode(entry, adapter)
        except ValueError as exc:
            logger.warning("Shared cache value invalid", extra={"cache_error": type(exc).__name__})
            entry = None
        else:
            if time.time() < entry.fresh_until:
                return value
    else:
        entry = None

    task_key = (key, id(asyncio.get_running_loop()))
    task = _REFRESHES.get(task_key)
    if task is None or task.done():

        async def load() -> T:
            result = await loader()
            await set_cached(
                key,
                result,
                freshness(result) if freshness else ttl_seconds,
                adapter=adapter,
                max_age=lifetime,
            )
            return result

        task = asyncio.create_task(load())
        _REFRESHES[task_key] = task

        def completed(done: asyncio.Task[object]) -> None:
            if _REFRESHES.get(task_key) is done:
                _REFRESHES.pop(task_key, None)
            if not done.cancelled() and (error := done.exception()) is not None:
                logger.warning(
                    "Shared cache loader failed", extra={"cache_error": type(error).__name__}
                )

        task.add_done_callback(completed)
    if entry is not None:
        return cast(T, value)
    return cast(T, await asyncio.shield(task))
