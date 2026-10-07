import asyncio
from unittest.mock import AsyncMock

import httpx
from langgraph_sdk.errors import ConflictError

from openswe.utils.background_task_state import update_background_task_state


async def test_concurrent_task_updates_read_after_acquiring_lock() -> None:
    client = AsyncMock()
    stored: dict[str, object] = {"running_background_tasks": ["cmd-old"], "source": "slack"}
    locked = False
    first_read = asyncio.Event()
    contended = asyncio.Event()

    async def create(*, thread_id: str, if_exists: str, ttl: int, metadata: object) -> None:
        nonlocal locked
        if locked:
            contended.set()
            response = httpx.Response(409, request=httpx.Request("POST", "http://test/threads"))
            raise ConflictError("already exists", response=response, body=None)
        locked = True

    async def delete(thread_id: str) -> None:
        nonlocal locked
        locked = False

    async def get(thread_id: str) -> dict[str, object]:
        assert locked
        snapshot = dict(stored)
        if not first_read.is_set():
            first_read.set()
            await contended.wait()
        return {"metadata": snapshot}

    async def update(thread_id: str, *, metadata: dict[str, object]) -> None:
        assert locked
        stored.update(metadata)

    client.threads.create.side_effect = create
    client.threads.delete.side_effect = delete
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
    assert not locked
