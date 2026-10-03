"""Maintenance must not hold up event delivery."""

import asyncio

import pytest

from agent.analytics import worker


async def test_delivery_continues_during_retention_and_shutdown_waits_for_both(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    retention_started = asyncio.Event()
    retention_finished = asyncio.Event()
    delivered = asyncio.Event()
    monkeypatch.setattr(worker, "_STOP", asyncio.Event())
    monkeypatch.setattr(worker, "configured", lambda: True)

    async def retention() -> bool:
        retention_started.set()
        await retention_finished.wait()
        return True

    async def deliver() -> int:
        await retention_started.wait()
        delivered.set()
        return 0

    async def summarize(*, limit: int) -> int:
        return 0

    monkeypatch.setattr(worker, "enforce_retention", retention)
    monkeypatch.setattr(worker, "deliver_batch", deliver)
    monkeypatch.setattr(worker, "recompute_dirty_partitions", summarize)
    await worker.start_worker()
    try:
        await asyncio.wait_for(delivered.wait(), timeout=1)
    finally:
        retention_finished.set()
        await asyncio.wait_for(worker.stop_worker(), timeout=1)
