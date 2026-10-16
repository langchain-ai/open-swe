import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock

import pytest

from openswe import event_claims
from openswe.utils.background_task_state import update_background_task_state
from tests.conftest import MemoryEventClaims


async def test_concurrent_task_updates_read_after_acquiring_lock(
    monkeypatch: pytest.MonkeyPatch, memory_event_claims: MemoryEventClaims
) -> None:
    client = AsyncMock()
    stored: dict[str, object] = {"running_background_tasks": ["cmd-old"], "source": "slack"}
    first_read = asyncio.Event()
    contended = asyncio.Event()

    async def claim(scope: str, key: str, *, ttl: timedelta) -> bool:
        acquired = await memory_event_claims.claim(scope, key, ttl=ttl)
        if not acquired:
            contended.set()
        return acquired

    async def get(thread_id: str) -> dict[str, object]:
        assert memory_event_claims.held
        snapshot = dict(stored)
        if not first_read.is_set():
            first_read.set()
            await contended.wait()
        return {"metadata": snapshot}

    async def update(thread_id: str, *, metadata: dict[str, object]) -> None:
        assert memory_event_claims.held
        stored.update(metadata)

    monkeypatch.setattr(event_claims, "claim", claim)
    client.threads.get.side_effect = get
    client.threads.update.side_effect = update

    async with asyncio.timeout(5):
        monitor = asyncio.create_task(
            update_background_task_state(client, "thread-1", finished=["cmd-old"])
        )
        await first_read.wait()
        launch = asyncio.create_task(
            update_background_task_state(client, "thread-1", running=["cmd-new"])
        )
        await asyncio.gather(monitor, launch)

    assert stored == {"running_background_tasks": ["cmd-new"], "source": "slack"}
    assert client.threads.get.await_count == 2
    assert client.threads.update.await_count == 2
    assert not memory_event_claims.held
