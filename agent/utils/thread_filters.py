"""Thread metadata keys the sidebar can filter on with a containment search.

The platform's thread search matches metadata by JSONB containment only: no OR,
no negation, no case folding, and no way to ask for an absent key. Predicates
the sidebar needs — "unresolved", "in this repo", "has no repo", "not an
automation" — are none of those shapes on their own, so they are derived here
and written onto the thread. Every one of them is then a single equality the
search can answer.
"""

import logging
import time
from collections.abc import Mapping
from typing import Any

from agent.store import get_value, now_ms, put_value

logger = logging.getLogger(__name__)

REPO_KEY = "repo_key"
AUTOMATION_KEY = "is_automation"
RESOLVED_KEY = "resolved"
FILTER_METADATA_KEYS = (REPO_KEY, AUTOMATION_KEY, RESOLVED_KEY)


def repo_key(owner: str | None, name: str | None) -> str:
    """``owner/name`` folded to lower case, or ``""`` when the thread has no repo.

    Case folding is what makes the key usable: repo metadata is compared
    case-insensitively everywhere else, and containment cannot do that.
    """
    if not isinstance(owner, str) or not isinstance(name, str):
        return ""
    owner, name = owner.strip().lower(), name.strip().lower()
    return f"{owner}/{name}" if owner and name else ""


def metadata_repo_key(metadata: Mapping[str, Any]) -> str:
    owner, name = metadata.get("repo_owner"), metadata.get("repo_name")
    if not (isinstance(owner, str) and owner and isinstance(name, str) and name):
        repo = metadata.get("repo")
        if isinstance(repo, Mapping):
            owner, name = repo.get("owner"), repo.get("name")
    return repo_key(
        owner if isinstance(owner, str) else None, name if isinstance(name, str) else None
    )


def _metadata_string(metadata: Mapping[str, Any], key: str) -> str | None:
    value = metadata.get(key)
    return value.strip() if isinstance(value, str) and value.strip() else None


def is_automation_thread(metadata: Mapping[str, Any]) -> bool:
    return (
        _metadata_string(metadata, "thread_category") == "automation"
        or _metadata_string(metadata, "source") == "schedule"
        or _metadata_string(metadata, "schedule_id") is not None
    )


def derive_filter_metadata(metadata: Mapping[str, Any]) -> dict[str, str | bool]:
    """The searchable keys implied by everything else on a thread.

    Idempotent, so creation paths, update paths and the backfill can all apply it
    to whatever view of the metadata they hold.
    """
    return {
        REPO_KEY: metadata_repo_key(metadata),
        AUTOMATION_KEY: is_automation_thread(metadata),
        RESOLVED_KEY: metadata.get(RESOLVED_KEY) is True,
    }


def filter_metadata_is_current(metadata: Mapping[str, Any]) -> bool:
    derived = derive_filter_metadata(metadata)
    return all(metadata.get(key) == value for key, value in derived.items())


FILTER_BACKFILL_NAMESPACE = ["thread_filter_backfill"]
FILTER_BACKFILL_KEY = "state"
_BACKFILL_CACHE_SECONDS = 60.0
_backfill_cache: tuple[float, bool] | None = None


def filter_backfill_record(*, threads_scanned: int, threads_updated: int) -> dict[str, int]:
    return {
        "completed_at_ms": now_ms(),
        "threads_scanned": threads_scanned,
        "threads_updated": threads_updated,
    }


async def mark_filter_backfill_complete(*, threads_scanned: int, threads_updated: int) -> None:
    global _backfill_cache
    await put_value(
        FILTER_BACKFILL_NAMESPACE,
        FILTER_BACKFILL_KEY,
        filter_backfill_record(threads_scanned=threads_scanned, threads_updated=threads_updated),
    )
    _backfill_cache = None


async def filter_pushdown_enabled() -> bool:
    """Whether every thread carries the derived keys, so filters can be pushed down.

    Pushing a filter into the search before the backfill has run would hide every
    thread that predates it, so the scan stays in charge until the backfill says
    otherwise. Cached because it gates each thread-list request.
    """
    global _backfill_cache
    cached = _backfill_cache
    if cached is not None and time.monotonic() - cached[0] < _BACKFILL_CACHE_SECONDS:
        return cached[1]
    try:
        record = await get_value(FILTER_BACKFILL_NAMESPACE, FILTER_BACKFILL_KEY)
        enabled = isinstance(record, Mapping) and isinstance(record.get("completed_at_ms"), int)
    except Exception:  # noqa: BLE001
        # Falling back to the scan is correct but slow, so hold the last answer
        # rather than asking a broken store on every request.
        logger.debug("Could not read the thread filter backfill state", exc_info=True)
        enabled = cached[1] if cached is not None else False
    _backfill_cache = (time.monotonic(), enabled)
    return enabled


def reset_filter_pushdown_cache() -> None:
    global _backfill_cache
    _backfill_cache = None
