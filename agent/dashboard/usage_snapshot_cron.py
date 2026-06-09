"""Global cron + on-demand scheduling for the usage-snapshot builder.

One global cron rebuilds every period's snapshot ~every 10 min (staggered off
:00). Registration is idempotent and concrete (mirrors ``analyzer_cron``): we
look for an existing cron tagged ``{"kind": "usage_snapshot"}`` before creating
one, and stash the ``cron_id`` under ``["agent_usage", "meta"] / "cron"``.
"""

from __future__ import annotations

import logging
from typing import Any

from .review_style_jobs import _client

logger = logging.getLogger(__name__)

_ASSISTANT_ID = "usage_snapshot"
_CRON_KIND = "usage_snapshot"
_SCHEDULE = "7,17,27,37,47,57 * * * *"  # ~every 10 min, staggered off :00
_META_NAMESPACE: list[str] = ["agent_usage", "meta"]
_META_CRON_KEY = "cron"


async def _existing_cron_id() -> str | None:
    item = await _client().store.get_item(_META_NAMESPACE, _META_CRON_KEY)
    value = item.get("value") if isinstance(item, dict) else getattr(item, "value", None)
    cron_id = value.get("cron_id") if isinstance(value, dict) else None
    return cron_id if isinstance(cron_id, str) and cron_id else None


async def ensure_usage_snapshot_cron() -> str | None:
    """Idempotently register the global usage-snapshot cron. Returns its id."""
    try:
        existing = await _existing_cron_id()
    except Exception:
        logger.debug("Could not read usage snapshot cron meta", exc_info=True)
        existing = None
    if existing:
        return existing

    try:
        crons = await _client().crons.search(metadata={"kind": _CRON_KIND}, limit=1)
        found = crons[0] if crons else None
        if found:
            cron_id = found.get("cron_id") if isinstance(found, dict) else None
            if isinstance(cron_id, str) and cron_id:
                await _store_cron_id(cron_id)
                return cron_id
    except Exception:
        logger.debug("Usage snapshot cron search failed", exc_info=True)

    try:
        cron = await _client().crons.create(
            _ASSISTANT_ID,
            schedule=_SCHEDULE,
            metadata={"kind": _CRON_KIND},
        )
    except Exception:
        logger.exception("Failed to create usage snapshot cron")
        return None

    cron_id = cron.get("cron_id") if isinstance(cron, dict) else getattr(cron, "cron_id", None)
    if isinstance(cron_id, str) and cron_id:
        await _store_cron_id(cron_id)
        return cron_id
    return None


async def _store_cron_id(cron_id: str) -> None:
    try:
        await _client().store.put_item(_META_NAMESPACE, _META_CRON_KEY, {"cron_id": cron_id})
    except Exception:
        logger.debug("Could not persist usage snapshot cron id", exc_info=True)


async def trigger_usage_snapshot_build() -> dict[str, Any]:
    """Fire-and-forget: schedule (not execute) one immediate build run."""
    try:
        await _client().runs.create(None, _ASSISTANT_ID)
    except Exception:
        logger.debug("Could not schedule immediate usage snapshot build", exc_info=True)
        return {"status": "error"}
    return {"status": "scheduled"}
