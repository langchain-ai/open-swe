from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.server import (
    SANDBOX_BACKENDS,
    SANDBOX_CREATING,
    SANDBOX_CREATION_TIMEOUT,
    ensure_sandbox_for_thread,
)


@pytest.mark.asyncio
async def test_stale_sandbox_creating_without_cached_backend_resets_and_creates() -> None:
    thread_id = "thread-stale-creating"
    SANDBOX_BACKENDS.clear()
    sandbox_backend = MagicMock()
    sandbox_backend.id = "sandbox-new"

    stale_at = 0.0  # epoch 0 → far older than the timeout
    thread = {"metadata": {"sandbox_id": SANDBOX_CREATING, "sandbox_creating_at": stale_at}}

    with (
        patch(
            "agent.server.get_sandbox_id_from_metadata",
            new_callable=AsyncMock,
            return_value=SANDBOX_CREATING,
        ),
        patch("agent.server.client.threads.get", new_callable=AsyncMock, return_value=thread),
        patch("agent.server._create_sandbox_with_proxy", new_callable=AsyncMock) as create_sandbox,
        patch("agent.server._configure_git_identity", new_callable=AsyncMock),
        patch("agent.server.client.threads.update", new_callable=AsyncMock) as update_thread,
    ):
        create_sandbox.return_value = sandbox_backend

        result = await ensure_sandbox_for_thread(thread_id)

    assert result.id == "sandbox-new"
    create_sandbox.assert_awaited_once()
    # Stale sentinel reset (id + timestamp cleared), then fresh creation claims it.
    assert update_thread.await_args_list[0].kwargs == {
        "thread_id": thread_id,
        "metadata": {"sandbox_id": None, "sandbox_creating_at": None},
    }
    assert update_thread.await_args_list[1].kwargs["metadata"]["sandbox_id"] == SANDBOX_CREATING
    assert update_thread.await_args_list[2].kwargs == {
        "thread_id": thread_id,
        "metadata": {"sandbox_id": "sandbox-new"},
    }

    SANDBOX_BACKENDS.clear()


@pytest.mark.asyncio
async def test_fresh_sandbox_creating_waits_for_other_worker() -> None:
    """A recent sentinel from another worker must be waited on, not overwritten."""
    thread_id = "thread-concurrent-creating"
    SANDBOX_BACKENDS.clear()
    existing_backend = MagicMock()
    existing_backend.id = "sandbox-existing"

    import time

    fresh_at = time.time()  # well within the timeout
    threads = [
        {"metadata": {"sandbox_id": SANDBOX_CREATING, "sandbox_creating_at": fresh_at}},
        {"metadata": {"sandbox_id": "sandbox-existing", "sandbox_creating_at": fresh_at}},
    ]

    async def passthrough(
        sb, _thread_id, _github_proxy_token=None, _github_proxy_repositories=None
    ):
        return sb

    with (
        patch(
            "agent.server.get_sandbox_id_from_metadata",
            new_callable=AsyncMock,
            return_value=SANDBOX_CREATING,
        ),
        patch("agent.server.client.threads.get", new_callable=AsyncMock, side_effect=threads),
        patch("agent.server.asyncio.sleep", new_callable=AsyncMock),
        patch("agent.server.create_sandbox", return_value=existing_backend) as connect_sandbox,
        patch("agent.server._create_sandbox_with_proxy", new_callable=AsyncMock) as create_sandbox,
        patch("agent.server.check_or_recreate_sandbox", side_effect=passthrough),
        patch("agent.server._refresh_github_proxy_or_recreate", side_effect=passthrough),
        patch("agent.server._configure_git_identity", new_callable=AsyncMock),
        patch("agent.server.client.threads.update", new_callable=AsyncMock) as update_thread,
    ):
        result = await ensure_sandbox_for_thread(thread_id)

    assert result.id == "sandbox-existing"
    # Connected to the worker's sandbox; no second sandbox was created.
    connect_sandbox.assert_called_once_with("sandbox-existing")
    create_sandbox.assert_not_awaited()
    # The fresh sentinel was never reset.
    for call in update_thread.await_args_list:
        assert call.kwargs["metadata"] != {"sandbox_id": None, "sandbox_creating_at": None}

    assert SANDBOX_CREATION_TIMEOUT > 0
    SANDBOX_BACKENDS.clear()
