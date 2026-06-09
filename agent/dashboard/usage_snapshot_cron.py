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
# A StateGraph run with no input raises EmptyInputError at __start__, so every
# scheduled run must carry an explicit empty input. rev marks crons created
# with the correct payload; older revs are reaped and recreated.
_CRON_INPUT: dict[str, Any] = {}
_CRON_REV = 2
_SCHEDULE = "7,17,27,37,47,57 * * * *"  # ~every 10 min, staggered off :00
_META_NAMESPACE: list[str] = ["agent_usage", "meta"]
_META_CRON_KEY = "cron"

# Once we've confirmed a cron is registered, steady-state requests skip the
# loopback check entirely — there is nothing left to re-verify.
_registered_cron_id: str | None = None


def _cron_id_of(cron: Any) -> str | None:
    cron_id = cron.get("cron_id") if isinstance(cron, dict) else getattr(cron, "cron_id", None)
    return cron_id if isinstance(cron_id, str) and cron_id else None


def _cron_rev_of(cron: Any) -> int:
    metadata = cron.get("metadata") if isinstance(cron, dict) else getattr(cron, "metadata", None)
    rev = metadata.get("rev") if isinstance(metadata, dict) else None
    return rev if isinstance(rev, int) else 1


async def _existing_cron_id() -> str | None:
    item = await _client().store.get_item(_META_NAMESPACE, _META_CRON_KEY)
    value = item.get("value") if isinstance(item, dict) else getattr(item, "value", None)
    if not isinstance(value, dict) or value.get("rev") != _CRON_REV:
        return None
    cron_id = value.get("cron_id")
    return cron_id if isinstance(cron_id, str) and cron_id else None


async def ensure_usage_snapshot_cron() -> str | None:
    """Idempotently register the global usage-snapshot cron. Returns its id."""
    global _registered_cron_id
    if _registered_cron_id:
        return _registered_cron_id

    try:
        existing = await _existing_cron_id()
    except Exception:
        logger.debug("Could not read usage snapshot cron meta", exc_info=True)
        existing = None
    if existing:
        _registered_cron_id = existing
        return existing

    try:
        crons = await _client().crons.search(metadata={"kind": _CRON_KIND}, limit=10)
        current = [
            cid for c in (crons or []) if (cid := _cron_id_of(c)) and _cron_rev_of(c) == _CRON_REV
        ]
        stale = [
            cid for c in (crons or []) if (cid := _cron_id_of(c)) and _cron_rev_of(c) != _CRON_REV
        ]
        # Stale revs were created without input and fail at __start__; replace them.
        await _delete_crons(stale)
        if current:
            keep = current[0]
            # search-then-create isn't atomic; concurrent replicas can each
            # create one. Reap any extras so we don't fire the build N times.
            await _delete_crons(current[1:])
            await _store_cron_id(keep)
            _registered_cron_id = keep
            return keep
    except Exception:
        logger.debug("Usage snapshot cron search failed", exc_info=True)

    try:
        cron = await _client().crons.create(
            _ASSISTANT_ID,
            schedule=_SCHEDULE,
            input=_CRON_INPUT,
            metadata={"kind": _CRON_KIND, "rev": _CRON_REV},
        )
    except Exception:
        logger.exception("Failed to create usage snapshot cron")
        return None

    cron_id = _cron_id_of(cron)
    if cron_id:
        await _store_cron_id(cron_id)
        _registered_cron_id = cron_id
        return cron_id
    return None


async def _delete_crons(cron_ids: list[str]) -> None:
    for cron_id in cron_ids:
        try:
            await _client().crons.delete(cron_id)
        except Exception:
            logger.debug("Could not delete duplicate usage cron %s", cron_id, exc_info=True)


async def _store_cron_id(cron_id: str) -> None:
    try:
        await _client().store.put_item(
            _META_NAMESPACE, _META_CRON_KEY, {"cron_id": cron_id, "rev": _CRON_REV}
        )
    except Exception:
        logger.debug("Could not persist usage snapshot cron id", exc_info=True)


async def trigger_usage_snapshot_build() -> dict[str, Any]:
    """Fire-and-forget: schedule (not execute) one immediate build run."""
    try:
        await _client().runs.create(None, _ASSISTANT_ID, input=_CRON_INPUT)
    except Exception:
        logger.debug("Could not schedule immediate usage snapshot build", exc_info=True)
        return {"status": "error"}
    return {"status": "scheduled"}
