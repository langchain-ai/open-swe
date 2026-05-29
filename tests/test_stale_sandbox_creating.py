from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.server import SANDBOX_BACKENDS, SANDBOX_CREATING, ensure_sandbox_for_thread


@pytest.mark.asyncio
async def test_stale_sandbox_creating_without_cached_backend_resets_and_creates() -> None:
    thread_id = "thread-stale-creating"
    SANDBOX_BACKENDS.clear()
    sandbox_backend = MagicMock()
    sandbox_backend.id = "sandbox-new"

    with (
        patch(
            "agent.server.get_sandbox_id_from_metadata",
            new_callable=AsyncMock,
            return_value=SANDBOX_CREATING,
        ),
        patch("agent.server._create_sandbox_with_proxy", new_callable=AsyncMock) as create_sandbox,
        patch("agent.server._configure_git_identity", new_callable=AsyncMock),
        patch("agent.server.client.threads.update", new_callable=AsyncMock) as update_thread,
    ):
        create_sandbox.return_value = sandbox_backend

        result = await ensure_sandbox_for_thread(thread_id)

    assert result.id == "sandbox-new"
    create_sandbox.assert_awaited_once()
    assert update_thread.await_args_list[0].kwargs == {
        "thread_id": thread_id,
        "metadata": {"sandbox_id": None},
    }
    assert update_thread.await_args_list[1].kwargs == {
        "thread_id": thread_id,
        "metadata": {"sandbox_id": SANDBOX_CREATING},
    }
    assert update_thread.await_args_list[2].kwargs == {
        "thread_id": thread_id,
        "metadata": {"sandbox_id": "sandbox-new"},
    }

    SANDBOX_BACKENDS.clear()
