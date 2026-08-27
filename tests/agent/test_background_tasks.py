from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from agent import background_tasks


async def test_monitor_swallows_cron_deletion_error() -> None:
    client = AsyncMock()
    client.threads.get.return_value = {"metadata": {}}

    with (
        patch("agent.background_tasks._client", return_value=client),
        patch(
            "agent.background_tasks._delete_crons",
            AsyncMock(side_effect=RuntimeError("cron service unavailable")),
        ),
    ):
        result = await background_tasks.monitor_background_tasks("thread-1")

    assert result == {"status": "missing_sandbox"}


async def test_monitor_deletes_cron_when_monitor_lock_cannot_be_acquired() -> None:
    backend = AsyncMock()
    backend.aexecute.return_value = SimpleNamespace(exit_code=1)
    client = AsyncMock()
    client.threads.get.return_value = {"metadata": {"sandbox_id": "sandbox-1"}}

    with (
        patch("agent.background_tasks._client", return_value=client),
        patch("agent.background_tasks.create_sandbox", AsyncMock(return_value=backend)),
        patch("agent.background_tasks._list_tasks", AsyncMock(return_value=[])),
        patch("agent.background_tasks._delete_crons", AsyncMock()) as delete_crons,
    ):
        await background_tasks.monitor_background_tasks("thread-1")

    delete_crons.assert_awaited_once_with("thread-1")


async def test_monitor_deletes_after_max_idle_ticks_when_sandbox_unreachable() -> None:
    metadata = {"sandbox_id": "sandbox-1", "background_task_idle_ticks": 0}
    client = AsyncMock()
    client.threads.get.side_effect = lambda _thread_id: {"metadata": dict(metadata)}

    async def update_metadata(*, thread_id: str, metadata: dict[str, int]) -> None:
        del thread_id
        test_metadata.update(metadata)

    test_metadata = metadata
    client.threads.update.side_effect = update_metadata

    with (
        patch("agent.background_tasks._client", return_value=client),
        patch(
            "agent.background_tasks.create_sandbox",
            AsyncMock(side_effect=RuntimeError("sandbox unavailable")),
        ),
        patch("agent.background_tasks._delete_crons", AsyncMock()) as delete_crons,
    ):
        for _ in range(background_tasks.MAX_IDLE_TICKS + 1):
            await background_tasks.monitor_background_tasks("thread-1")

    delete_crons.assert_awaited_once_with("thread-1")
    assert metadata["background_task_idle_ticks"] == background_tasks.MAX_IDLE_TICKS + 1
