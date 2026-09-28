"""Coordinate analytics delivery, summaries, and retention."""

import asyncio
import logging

from agent.analytics.outbox import deliver_batch
from agent.analytics.retention import enforce_retention
from agent.analytics.summaries import recompute_dirty_partitions
from agent.database import configured

logger = logging.getLogger(__name__)

_STOP = asyncio.Event()
_WORKER: asyncio.Task[None] | None = None
_RETENTION_WORKER: asyncio.Task[None] | None = None


async def run_worker() -> None:
    while not _STOP.is_set():
        try:
            delivered = await deliver_batch()
            await recompute_dirty_partitions(limit=20)
        except Exception:  # noqa: BLE001
            delivered = 0
            logger.warning("Analytics worker iteration failed", exc_info=True)
        try:
            await asyncio.wait_for(_STOP.wait(), timeout=0.25 if delivered else 5.0)
        except TimeoutError:
            pass


async def run_retention_worker() -> None:
    while not _STOP.is_set():
        try:
            await enforce_retention()
        except Exception:  # noqa: BLE001
            logger.warning("Analytics retention failed", exc_info=True)
        try:
            await asyncio.wait_for(_STOP.wait(), timeout=60.0)
        except TimeoutError:
            continue


async def start_worker() -> None:
    global _WORKER, _RETENTION_WORKER
    if not configured():
        return
    _STOP.clear()
    if _WORKER is None or _WORKER.done():
        _WORKER = asyncio.create_task(run_worker(), name="analytics-outbox-worker")
    if _RETENTION_WORKER is None or _RETENTION_WORKER.done():
        _RETENTION_WORKER = asyncio.create_task(
            run_retention_worker(), name="analytics-retention-worker"
        )


async def stop_worker() -> None:
    global _WORKER, _RETENTION_WORKER
    _STOP.set()
    workers = [worker for worker in (_WORKER, _RETENTION_WORKER) if worker is not None]
    if workers:
        await asyncio.gather(*workers)
    _WORKER = None
    _RETENTION_WORKER = None
