import asyncio
from collections.abc import Sequence
from unittest.mock import AsyncMock

import httpx
import pytest
from langgraph_sdk.errors import ConflictError

from agent.utils.background_task_state import update_background_task_state


@pytest.mark.parametrize(
    ("previous", "running", "finished", "reset", "expected", "writes"),
    [
        (None, [], [], False, [], 0),
        ([], [], [], True, [], 0),
        (["cmd-1"], ["cmd-1"], [], False, ["cmd-1"], 0),
        (["cmd-1"], [], ["cmd-missing"], False, ["cmd-1"], 0),
        (["cmd-1"], ["cmd-2"], ["cmd-1"], False, ["cmd-2"], 1),
        (["cmd-1"], [], [], True, [], 1),
    ],
)
async def test_task_state_only_writes_changes(
    previous: list[str] | None,
    running: Sequence[str],
    finished: Sequence[str],
    reset: bool,
    expected: list[str],
    writes: int,
) -> None:
    client = AsyncMock()
    metadata: dict[str, object] = {"sandbox_id": "sandbox-1"}
    if previous is not None:
        metadata["running_background_tasks"] = previous
    client.threads.get.return_value = {"metadata": metadata}

    result = await update_background_task_state(
        client, "thread-1", running=running, finished=finished, reset=reset
    )

    client.threads.get.assert_awaited_once_with("thread-1")
    assert client.threads.update.await_count == writes
    if writes:
        client.threads.update.assert_awaited_once_with(
            "thread-1", metadata={"running_background_tasks": expected}
        )
    assert result == {"sandbox_id": "sandbox-1", "running_background_tasks": expected}
    assert metadata.get("running_background_tasks") == previous


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
