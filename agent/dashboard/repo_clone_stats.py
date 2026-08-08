"""Ledger of repos Open SWE has cloned into a sandbox.

Every run that preps a repo bumps that repo's counter here. The nightly base
snapshot rebuild reads the ledger to decide which repos to pre-clone, so repos
the fleet actually works on stop paying the clone cost on every cold start.

Recording is best-effort and never raises: a failed ledger write must not take
down a run.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from langgraph_sdk import get_client

from .review_styles import normalize_repo_full_name

logger = logging.getLogger(__name__)

REPO_CLONE_STATS_NAMESPACE: list[str] = ["repo_clone_stats"]


def _client():
    return get_client()


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _parse_iso(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


async def record_repo_clone(owner: str | None, name: str | None) -> None:
    """Bump the clone counter for ``owner/name``. Never raises."""
    if not owner or not name:
        return
    try:
        full_name = normalize_repo_full_name(f"{owner}/{name}")
    except Exception:  # noqa: BLE001
        logger.debug("Skipping clone ledger for invalid repo %r/%r", owner, name)
        return

    try:
        item = await _client().store.get_item(REPO_CLONE_STATS_NAMESPACE, full_name)
        existing = item.get("value") if isinstance(item, dict) else getattr(item, "value", None)
        if not isinstance(existing, dict):
            existing = {}
        count = existing.get("clone_count")
        value = {
            "full_name": full_name,
            "clone_count": (count if isinstance(count, int) and count > 0 else 0) + 1,
            "first_cloned_at": existing.get("first_cloned_at") or _now_iso(),
            "last_cloned_at": _now_iso(),
        }
        await _client().store.put_item(REPO_CLONE_STATS_NAMESPACE, full_name, value)
    except Exception:  # noqa: BLE001
        logger.debug("Failed to record repo clone for %s", full_name, exc_info=True)


async def list_repo_clone_stats() -> list[dict[str, Any]]:
    try:
        result = await _client().store.search_items(REPO_CLONE_STATS_NAMESPACE, limit=1000)
    except Exception:  # noqa: BLE001
        logger.debug("repo clone stats lookup failed", exc_info=True)
        return []
    items = result.get("items") if isinstance(result, dict) else getattr(result, "items", [])
    out: list[dict[str, Any]] = []
    for item in items or []:
        value = item.get("value") if isinstance(item, dict) else getattr(item, "value", None)
        if isinstance(value, dict) and isinstance(value.get("full_name"), str):
            out.append(value)
    return out


async def repos_to_preclone(*, limit: int, max_age_days: int) -> list[str]:
    """Return the repos worth baking into the next snapshot, most-used first.

    Repos untouched for ``max_age_days`` drop out so the snapshot tracks what
    the fleet works on now rather than growing forever.
    """
    cutoff = datetime.now(UTC) - timedelta(days=max_age_days)

    fresh: list[tuple[int, datetime, str]] = []
    for record in await list_repo_clone_stats():
        last = _parse_iso(record.get("last_cloned_at"))
        if last is None or last < cutoff:
            continue
        count = record.get("clone_count")
        fresh.append((count if isinstance(count, int) else 0, last, record["full_name"]))

    fresh.sort(key=lambda row: (-row[0], -row[1].timestamp(), row[2]))
    return [full_name for _count, _last, full_name in fresh[:limit]]
