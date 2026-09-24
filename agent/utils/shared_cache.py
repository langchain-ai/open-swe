"""Share cached values across queue workers through the LangGraph Store.

Turns rarely land on the same queue worker twice, so a per-worker cache is
usually cold; every read goes to the Store instead, where any worker's value is
visible. Values are opaque to the Store, so callers round-trip them through
their own ``dump``/``load``.

With ``serve_stale``, an item older than ``ttl_seconds`` is still served while
one background refresh replaces it; one older than ``max_stale_seconds`` is a
miss, so the caller waits for the loader, and the Store deletes it. Without it,
a stale item is a miss and the Store deletes it once stale, for values that must
not outlive the check that produced them.

This sits on the agent's critical path. A Store read that fails or stalls, or
returns an item ``load`` cannot decode, falls back to the loader; a Store write
that fails or stalls still returns the value already computed. Every such
failure is logged, never raised.
"""

import asyncio
import hashlib
import json
import logging
import math
import time
from collections.abc import Awaitable, Callable, Sequence

from agent.store import get_value, put_value

logger = logging.getLogger(__name__)

# Store round trips sit on the critical path; past this long a slow Store is
# treated like a failing one.
_STORE_TIMEOUT_SECONDS = 2.0

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


async def cached[T](
    namespace: Sequence[str],
    key: str,
    ttl_seconds: float,
    loader: Callable[[], Awaitable[T]],
    *,
    dump: Callable[[T], object],
    load: Callable[[object], T],
    max_stale_seconds: float = 86400.0,
    serve_stale: bool = True,
) -> T:
    item_key = hashlib.sha256(key.encode()).hexdigest()
    log_extra = {"namespace": list(namespace), "key": key}
    usable_seconds = max_stale_seconds if serve_stale else ttl_seconds

    async def _write(value: T) -> None:
        try:
            async with asyncio.timeout(_STORE_TIMEOUT_SECONDS):
                await put_value(
                    namespace,
                    item_key,
                    {"stored_at": _now(), "value": dump(value)},
                    ttl_minutes=math.ceil(usable_seconds / 60),
                )
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
        await _write(value)

    def _schedule_refresh() -> None:
        task_key = (json.dumps([*namespace, key]), id(asyncio.get_running_loop()))
        existing = _REFRESH_TASKS.get(task_key)
        if existing is not None and not existing.done():
            return
        task = asyncio.create_task(_refresh())
        _REFRESH_TASKS[task_key] = task
        task.add_done_callback(lambda _task: _REFRESH_TASKS.pop(task_key, None))

    try:
        async with asyncio.timeout(_STORE_TIMEOUT_SECONDS):
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
            # On the critical path: an item this version cannot decode must not
            # break callers; recompute and overwrite it instead. Only the error's
            # type is logged: a validation error echoes the stored value, which
            # can hold personal data such as an email.
            logger.warning(
                "Shared cache store item is not decodable",
                extra={**log_extra, "error_type": type(exc).__name__},
            )
        else:
            if age < ttl_seconds:
                return value
            if age < usable_seconds:
                _schedule_refresh()
                return value
            # Too old to serve: treat it as a miss.

    value = await loader()
    await _write(value)
    return value
