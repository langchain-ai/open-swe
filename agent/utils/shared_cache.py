"""Share cached values across queue workers through the LangGraph Store.

:func:`agent.utils.ttl_cache.cached` sits in front so a worker that already
computed a value skips the Store entirely; behind it, the Store lets a worker
with a cold in-process cache reuse a value another worker already computed.
Values are opaque to the Store, so callers round-trip them through their own
``dump``/``load``.

An item older than ``ttl_seconds`` is still served while one background
refresh replaces it in both the Store and this worker's front cache; one older
than ``max_stale_seconds`` is a miss, so the caller waits for the loader.

This sits on the agent's critical path. A Store read that fails, or returns
an item ``load`` cannot decode, falls back to the loader; a Store write that
fails still returns the value already computed. Every such failure is
logged, never raised.
"""

import asyncio
import hashlib
import json
import logging
import time
from collections.abc import Awaitable, Callable, Sequence

from agent.store import get_value, put_value
from agent.utils import ttl_cache

logger = logging.getLogger(__name__)

_REFRESH_TASKS: dict[tuple[str, int], asyncio.Task[None]] = {}


def _now() -> float:
    return time.time()


def clear() -> None:
    """Cancel and forget pending background refreshes."""
    for task in _REFRESH_TASKS.values():
        # A task whose loop has closed can never run again, and cancelling it
        # would schedule onto that closed loop.
        if not task.get_loop().is_closed():
            task.cancel()
    _REFRESH_TASKS.clear()


def _front_key(namespace: Sequence[str], key: str) -> str:
    return "shared:" + json.dumps([*namespace, key])


async def cached[T](
    namespace: Sequence[str],
    key: str,
    ttl_seconds: float,
    loader: Callable[[], Awaitable[T]],
    *,
    dump: Callable[[T], object],
    load: Callable[[object], T],
    max_stale_seconds: float = 86400.0,
) -> T:
    item_key = hashlib.sha256(key.encode()).hexdigest()
    front_key = _front_key(namespace, key)
    log_extra = {"namespace": list(namespace), "key": key}

    async def _write(value: T) -> None:
        try:
            await put_value(namespace, item_key, {"stored_at": _now(), "value": dump(value)})
        except Exception:
            # On the critical path: a Store outage must not fail a call that
            # already has a value to return.
            logger.warning("Shared cache store write failed", exc_info=True, extra=log_extra)

    async def _refresh() -> None:
        try:
            value = await loader()
        except Exception:
            logger.warning("Shared cache background refresh failed", exc_info=True, extra=log_extra)
            return
        # The stale read that started this refresh left the old value in the
        # front cache for a full ttl; replace it now that the new one is known.
        ttl_cache.set_cached(front_key, value, ttl_seconds)
        await _write(value)

    def _schedule_refresh() -> None:
        task_key = (front_key, id(asyncio.get_running_loop()))
        existing = _REFRESH_TASKS.get(task_key)
        if existing is not None and not existing.done():
            return
        task = asyncio.create_task(_refresh())
        _REFRESH_TASKS[task_key] = task
        task.add_done_callback(lambda _task: _REFRESH_TASKS.pop(task_key, None))

    async def _resolve() -> T:
        try:
            item = await get_value(namespace, item_key)
        except Exception:
            # On the critical path: a Store outage falls back to the loader.
            logger.warning("Shared cache store read failed", exc_info=True, extra=log_extra)
            item = None

        if item is not None:
            try:
                value = load(item["value"])
                age = _now() - item["stored_at"]
            except Exception as exc:
                # On the critical path: an item this version cannot decode must
                # not break callers; recompute and overwrite it instead. Only the
                # error's type is logged: a validation error echoes the stored
                # value, which can hold personal data such as an email.
                logger.warning(
                    "Shared cache store item is not decodable",
                    extra={**log_extra, "error_type": type(exc).__name__},
                )
            else:
                if age < ttl_seconds:
                    return value
                if age < max_stale_seconds:
                    _schedule_refresh()
                    return value
                # Too old to serve even while refreshing: treat it as a miss.

        value = await loader()
        await _write(value)
        return value

    return await ttl_cache.cached(front_key, ttl_seconds, _resolve)
