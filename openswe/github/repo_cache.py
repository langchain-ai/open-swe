"""Per-user cache of the repositories a person reaches through the GitHub App.

Listing them fans out to ``/user/installations`` plus a paginated
``/user/installations/{id}/repositories`` sweep per installation, which takes
10s+ for users whose App is installed across hundreds of repos. The listing is
cached per person in ``user_record``, where every replica sees it:

- The repo picker serves it stale-while-revalidate.
- ``accessible_repo_full_names`` is an authorization boundary (it decides which
  private PR metadata a person may see), so it trusts a listing for at most
  ``ACCESS_FRESH_MS``. Installation webhooks clear every listing, so a repo
  removed from the App drops at once; access lost on GitHub's side, such as
  leaving an org, shows within that window.

Keys are derived from the authenticated session login only, so a cached
listing is never served to a different user.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from openswe.github import repos
from openswe.users.records import UserRecords

logger = logging.getLogger(__name__)

REPO_LIST_CACHE = UserRecords("repo_list_cache")
REPO_LIST_FRESH_MS = 10 * 60 * 1000
REPO_LIST_MAX_AGE_MS = 24 * 60 * 60 * 1000
ACCESS_FRESH_MS = 60 * 1000

_refreshing: set[str] = set()
_refresh_tasks: set[asyncio.Task[None]] = set()


def _now_ms() -> int:
    return int(datetime.now(UTC).timestamp() * 1000)


def _cache_key(login: str) -> str:
    return login.strip().lower()


async def read_cached_repos(login: str) -> tuple[dict[str, Any], int] | None:
    """Return ``(payload, age_ms)`` for a login, or ``None`` when unusable.

    Fail-soft on purpose, both ways: this is a cache, so an unreachable database
    is a miss the caller serves by rebuilding the payload.
    """
    if not login.strip():
        return None
    try:
        value = await REPO_LIST_CACHE.get(login)
    except Exception:
        logger.warning("repo list cache lookup failed", exc_info=True)
        return None
    if value is None:
        return None
    payload = value.get("payload")
    cached_at_ms = value.get("cached_at_ms")
    if not isinstance(payload, dict) or not isinstance(cached_at_ms, int):
        return None
    age_ms = max(_now_ms() - cached_at_ms, 0)
    if age_ms > REPO_LIST_MAX_AGE_MS:
        return None
    return payload, age_ms


async def write_cached_repos(login: str, payload: dict[str, Any]) -> None:
    if not login.strip():
        return
    try:
        await REPO_LIST_CACHE.put(login, {"payload": payload, "cached_at_ms": _now_ms()})
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


async def build_repo_payload(login: str) -> dict[str, Any]:
    """List the person's installations and repos from GitHub now, and cache the listing."""
    installations, repositories = await repos.fetch_user_installations_and_repos(login)
    payload = {
        "installations": [
            {
                "id": i.get("id"),
                "account": (i.get("account") or {}).get("login"),
                "account_type": (i.get("account") or {}).get("type"),
            }
            for i in installations
        ],
        "repositories": [
            {
                "full_name": r.get("full_name"),
                "private": r.get("private", False),
                "archived": r.get("archived", False),
            }
            for r in repositories
            if r.get("full_name")
        ],
    }
    await write_cached_repos(login, payload)
    return payload


async def accessible_repo_full_names(login: str) -> frozenset[str]:
    """Lowercased ``owner/name`` of repos the user can access, as of ``ACCESS_FRESH_MS`` ago at most."""
    cached = await read_cached_repos(login)
    if cached is not None and cached[1] <= ACCESS_FRESH_MS:
        payload = cached[0]
    else:
        payload = await build_repo_payload(login)
    repositories = payload.get("repositories")
    return frozenset(
        repo["full_name"].lower()
        for repo in (repositories if isinstance(repositories, list) else [])
        if isinstance(repo, dict) and isinstance(repo.get("full_name"), str)
    )


async def forget_cached_repos() -> None:
    """Drop everyone's listing, for an installation or its repositories changed."""
    try:
        await REPO_LIST_CACHE.clear()
    except Exception:
        logger.warning("repo list cache clear failed", exc_info=True)
