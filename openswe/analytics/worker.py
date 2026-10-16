"""Coordinate analytics delivery, summaries, and retention."""

import asyncio
import logging

from openswe.analytics.outbox import deliver_batch
from openswe.analytics.retention import enforce_retention
from openswe.analytics.summaries import recompute_dirty_partitions
from openswe.database import configured

logger = logging.getLogger(__name__)

_RETENTION_INTERVAL_SECONDS = 3600.0

_STOP = asyncio.Event()
_WORKER: asyncio.Task[None] | None = None


async def run_worker() -> None:
    loop = asyncio.get_running_loop()
    retention_due = loop.time()
    while not _STOP.is_set():
        try:
            delivered = await deliver_batch()
            await recompute_dirty_partitions(limit=20)
            if loop.time() >= retention_due:
                retention_due = loop.time() + _RETENTION_INTERVAL_SECONDS
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
