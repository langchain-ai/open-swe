"""Coordinate analytics delivery, summaries, and retention."""

import asyncio
import logging

from agent.analytics.database import configured
from agent.analytics.outbox import deliver_batch
from agent.analytics.retention import enforce_retention
from agent.analytics.summaries import recompute_dirty_partitions

logger = logging.getLogger(__name__)

_STOP = asyncio.Event()
_WORKER: asyncio.Task[None] | None = None


async def run_worker() -> None:
    while not _STOP.is_set():
        try:
            delivered = await deliver_batch()
            await recompute_dirty_partitions(limit=20)
            await enforce_retention()
        except Exception:  # noqa: BLE001
            delivered = 0
            logger.warning("Analytics worker iteration failed", exc_info=True)
        try:
            await asyncio.wait_for(_STOP.wait(), timeout=0.25 if delivered else 5.0)
        except TimeoutError:
            pass


async def start_worker() -> None:
    global _WORKER
    if not configured() or (_WORKER is not None and not _WORKER.done()):
        return
    _STOP.clear()
    _WORKER = asyncio.create_task(run_worker(), name="analytics-outbox-worker")


async def stop_worker() -> None:
    global _WORKER
    _STOP.set()
    if _WORKER is not None:
        await _WORKER
    _WORKER = None
