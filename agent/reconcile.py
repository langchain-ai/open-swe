"""Safety-net reconciliation for registry lifecycle rows."""

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from .dashboard.thread_registry import RunStatus, ThreadRow, get_thread_registry
from .utils.thread_ops import langgraph_client

logger = logging.getLogger(__name__)
_PAGE_SIZE = 100


def _parse_created_at(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if not isinstance(value, str) or not value:
        return None
    text = f"{value[:-1]}+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


async def reconcile_stale_runs(*, max_age_seconds: int = 1800) -> dict[str, int]:
    """Correct stale queued/running rows from the cloud run or device heartbeat."""
    client = langgraph_client()
    registry = await get_thread_registry()
    now = datetime.now(UTC)
    threads_checked = 0
    stale_runs = 0
    corrected = 0

    async def reconcile(thread: ThreadRow) -> None:
        nonlocal threads_checked, stale_runs, corrected
        threads_checked += 1
        if (now - thread.status_at).total_seconds() <= max_age_seconds:
            return
        stale_runs += 1
        if not thread.status_run_id:
            return
        try:
            if thread.environment == "local":
                device = (
                    await registry.device(thread.device_id, thread.owner_login)
                    if thread.device_id
                    else None
                )
                last_seen = _parse_created_at(device.get("last_seen_at")) if device else None
                if last_seen and (now - last_seen).total_seconds() <= max_age_seconds:
                    return
                await registry.transition(
                    thread.id,
                    thread.status_run_id,
                    "error",
                    environment="local",
                    device_id=thread.device_id,
                    error="device went offline",
                )
                corrected += 1
                return
            run = await client.runs.get(thread.id, thread.status_run_id)
            raw_status = run.get("status") if isinstance(run, dict) else None
            status_map: dict[str, RunStatus] = {
                "pending": "queued",
                "running": "running",
                "success": "finished",
                "interrupted": "interrupted",
                "error": "error",
                "timeout": "error",
            }
            mapped = status_map.get(raw_status) if isinstance(raw_status, str) else None
            if mapped and mapped != thread.status:
                await registry.transition(
                    thread.id,
                    thread.status_run_id,
                    mapped,
                    environment="cloud",
                    error=str(raw_status) if mapped == "error" else None,
                )
                corrected += 1
        except Exception:
            logger.exception("Reconcile sweep: failed to reconcile thread %s", thread.id)

    for status in ("queued", "running"):
        cursor: str | None = None
        candidates: list[ThreadRow] = []
        while True:
            try:
                page = await registry.list(
                    None,
                    status=status,
                    limit=_PAGE_SIZE,
                    cursor=cursor,
                )
            except Exception:
                logger.exception("Reconcile sweep: registry query failed for status %s", status)
                break
            candidates.extend(page.items)
            if not page.has_more or not page.cursor:
                break
            cursor = page.cursor
        for thread in candidates:
            await reconcile(thread)

    events_pruned = await prune_thread_events()
    counts = {
        "threads_checked": threads_checked,
        "stale_runs": stale_runs,
        "corrected": corrected,
        "events_pruned": events_pruned,
    }
    logger.info("Reconcile sweep complete: %s", counts)
    return counts


async def prune_thread_events(*, max_age_hours: int = 24) -> int:
    registry = await get_thread_registry()
    return await registry.prune_events(
        older_than=datetime.now(UTC) - timedelta(hours=max_age_hours)
    )
