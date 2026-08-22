"""Per-user cache of the GitHub repo list behind the dashboard repo picker.

Building the picker payload fans out to ``/user/installations`` plus a
paginated ``/user/installations/{id}/repositories`` sweep per installation,
which takes 10s+ for users whose App is installed across hundreds of repos.
Installations change rarely, so the payload is cached per login in the
LangGraph store and served stale-while-revalidate.

Keys are derived from the authenticated session login only, so a cached
payload is never served to a different user. Only the picker listing reads
this cache — ``accessible_repo_full_names`` (an authorization boundary) stays
fresh on every call.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from ..store import get_value, now_ms, put_value

logger = logging.getLogger(__name__)

REPO_LIST_CACHE_NAMESPACE: list[str] = ["repo_list_cache"]
REPO_LIST_FRESH_MS = 10 * 60 * 1000
REPO_LIST_MAX_AGE_MS = 24 * 60 * 60 * 1000

_refreshing: set[str] = set()
_refresh_tasks: set[asyncio.Task[None]] = set()


def _cache_key(login: str) -> str:
    return login.strip().lower()


async def read_cached_repos(login: str) -> tuple[dict[str, Any], int] | None:
    """Return ``(payload, age_ms)`` for a login, or ``None`` when unusable.

    Fail-soft on purpose, both ways: this is a cache, so an unreachable store
    is a miss the caller serves by rebuilding the payload.
    """
    if not login.strip():
        return None
    try:
        value = await get_value(REPO_LIST_CACHE_NAMESPACE, _cache_key(login))
    except Exception:
        logger.warning("repo list cache lookup failed", exc_info=True)
        return None
    if value is None:
        return None
    payload = value.get("payload")
    cached_at_ms = value.get("cached_at_ms")
    if not isinstance(payload, dict) or not isinstance(cached_at_ms, int):
        return None
    age_ms = max(now_ms() - cached_at_ms, 0)
    if age_ms > REPO_LIST_MAX_AGE_MS:
        return None
    return payload, age_ms


async def write_cached_repos(login: str, payload: dict[str, Any]) -> None:
    if not login.strip():
        return
    try:
        await put_value(
            REPO_LIST_CACHE_NAMESPACE,
            _cache_key(login),
            {"payload": payload, "cached_at_ms": now_ms()},
        )
    except Exception:
        logger.warning("repo list cache write failed", exc_info=True)


def schedule_repo_cache_refresh(login: str, refresh: Callable[[], Awaitable[Any]]) -> None:
    """Rebuild a login's cached payload in the background, once at a time."""
    key = _cache_key(login)
    if not key or key in _refreshing:
        return

    async def run() -> None:
        try:
            await refresh()
        except Exception as e:
            logger.warning("background repo list refresh for %s failed: %s", key, e)
        finally:
            _refreshing.discard(key)

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    _refreshing.add(key)
    task = loop.create_task(run())
    _refresh_tasks.add(task)
    task.add_done_callback(_refresh_tasks.discard)
