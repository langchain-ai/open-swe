import asyncio
from collections.abc import Iterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openswe.sandboxes.lifecycle import HandedOver, hand_sandbox_to_mda
from openswe.sandboxes.state import SANDBOX_BACKENDS, set_sandbox_backend


@pytest.fixture(autouse=True)
def sandbox_client() -> Iterator[AsyncMock]:
    client = AsyncMock()
    with patch("openswe.sandboxes.providers.langsmith.get_async_sandbox_client") as factory:
        factory.return_value.__aenter__ = AsyncMock(return_value=client)
        yield client


@pytest.mark.asyncio
async def test_worker_cannot_recreate_shared_sandbox(sandbox_client: AsyncMock) -> None:
    SANDBOX_BACKENDS.clear()
    shared_sandbox = MagicMock(id="sandbox-shared")
    coordinator = set_sandbox_backend("coordinator", shared_sandbox)
    worker = set_sandbox_backend("worker", shared_sandbox)

    with (
        patch(
            "openswe.sandboxes.lifecycle.get_sandbox_metadata",
            new_callable=AsyncMock,
            return_value={
                "sandbox_id": "sandbox-shared",
                "task_id": "task-1",
                "sandbox_host_thread_id": "coordinator",
            },
        ),
        patch(
            "openswe.sandboxes.lifecycle.client.threads.update", new_callable=AsyncMock
        ) as update,
    ):
        with pytest.raises(PermissionError, match="coordinator must recover"):
            await hand_sandbox_to_mda("worker")

    sandbox_client.stop_sandbox.assert_not_awaited()
    update.assert_not_awaited()
    assert coordinator.current is shared_sandbox
    assert worker.current is shared_sandbox
    SANDBOX_BACKENDS.clear()


@pytest.mark.asyncio
@pytest.mark.parametrize("stop_failure", [None, RuntimeError("stop unavailable"), TimeoutError()])
async def test_recreating_an_old_sandbox_hands_the_thread_to_mda(
    sandbox_client: AsyncMock, stop_failure: Exception | None
) -> None:
    async def hanging_stop(_sandbox_id: str) -> None:
        await asyncio.Event().wait()

    sandbox_client.stop_sandbox.side_effect = (
        hanging_stop if isinstance(stop_failure, TimeoutError) else stop_failure
    )
    timeout = asyncio.timeout
    thread_id = "thread-recreate"
    SANDBOX_BACKENDS.clear()
    proxy = set_sandbox_backend(thread_id, MagicMock(id="sandbox-old"))

    with (
        patch("openswe.sandboxes.lifecycle.asyncio.timeout", side_effect=lambda _: timeout(0.01)),
        patch(
            "openswe.sandboxes.lifecycle.get_sandbox_metadata",
            new_callable=AsyncMock,
            return_value={"sandbox_id": "sandbox-old", "owner_login": "octocat"},
        ),
        patch(
            "openswe.sandboxes.lifecycle.client.threads.update", new_callable=AsyncMock
        ) as update,
    ):
        handed = await hand_sandbox_to_mda(thread_id, workspace_slug="large", source="base")

    sandbox_client.stop_sandbox.assert_awaited_once_with("sandbox-old")
    if stop_failure is None:
        stop_error = None
    elif isinstance(stop_failure, TimeoutError):
        stop_error = "Stopping the old sandbox timed out after 10 seconds"
    else:
        stop_error = str(stop_failure)
    assert handed == HandedOver("sandbox-old", stop_error)
    # MDA creates the next box from what was asked for; the thread no longer names the old one.
    assert [call.kwargs["metadata"] for call in update.await_args_list] == [
        {"sandbox_replacement": {"workspace": "large", "source": "base"}},
        {"sandbox_id": None, "sandbox_base_proxy_config": None},
    ]
    assert not proxy.has_backend
    SANDBOX_BACKENDS.clear()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("metadata", "refusal"),
    [
        ({"sandbox_id": "sandbox-mda", "sandbox_owner": "mda"}, "only it may replace"),
        ({"sandbox_id": "bridge:mac-1"}, "cannot be given a cloud sandbox"),
        ({}, "has no sandbox to recreate"),
    ],
)
async def test_only_an_old_cloud_sandbox_is_handed_over(
    sandbox_client: AsyncMock, metadata: dict[str, str], refusal: str
) -> None:
    SANDBOX_BACKENDS.clear()

    with (
        patch(
            "openswe.sandboxes.lifecycle.get_sandbox_metadata",
            new_callable=AsyncMock,
            return_value=metadata,
        ),
        patch(
            "openswe.sandboxes.lifecycle.client.threads.update", new_callable=AsyncMock
        ) as update,
        pytest.raises(ValueError, match=refusal),
    ):
        await hand_sandbox_to_mda("thread-refused")

    sandbox_client.stop_sandbox.assert_not_awaited()
    update.assert_not_awaited()
