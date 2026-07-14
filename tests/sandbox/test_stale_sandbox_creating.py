"""Simplified sandbox get-or-create-then-reconnect flow (no ``__creating__`` sentinel).

Dispatch uses ``multitask_strategy="interrupt"`` so a thread never provisions
two sandboxes concurrently; the cross-process sentinel poll was removed.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.server import SANDBOX_BACKENDS, ensure_sandbox_for_thread
from agent.utils.sandbox_state import get_or_create_sandbox_backend_proxy


@pytest.mark.asyncio
async def test_ensure_sandbox_creates_new_when_no_metadata() -> None:
    thread_id = "thread-new"
    SANDBOX_BACKENDS.clear()
    sandbox_backend = MagicMock()
    sandbox_backend.id = "sandbox-new"

    with (
        patch(
            "agent.server.get_sandbox_id_from_metadata",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch(
            "agent.server._create_sandbox_with_proxy",
            new_callable=AsyncMock,
            return_value=sandbox_backend,
        ) as create_sandbox,
        patch("agent.server._configure_git_identity", new_callable=AsyncMock),
        patch("agent.server.client.threads.update", new_callable=AsyncMock) as update_thread,
    ):
        result = await ensure_sandbox_for_thread(thread_id)

    assert result.id == "sandbox-new"
    create_sandbox.assert_awaited_once()
    # The new sandbox id is persisted to thread metadata (no sentinel writes).
    assert update_thread.await_args_list[-1].kwargs == {
        "thread_id": thread_id,
        "metadata": {"sandbox_id": "sandbox-new"},
    }
    SANDBOX_BACKENDS.clear()


@pytest.mark.asyncio
async def test_ensure_sandbox_reconnects_to_metadata_sandbox() -> None:
    thread_id = "thread-reconnect"
    SANDBOX_BACKENDS.clear()
    existing_backend = MagicMock()
    existing_backend.id = "sandbox-existing"

    async def passthrough(
        sandbox_backend,
        _thread_id,
        _github_proxy_token=None,
        _github_proxy_repositories=None,
        _repo=None,
    ):
        return sandbox_backend

    with (
        patch(
            "agent.server.get_sandbox_id_from_metadata",
            new_callable=AsyncMock,
            return_value="sandbox-existing",
        ),
        patch(
            "agent.server.create_sandbox",
            new_callable=AsyncMock,
            return_value=existing_backend,
        ) as connect_sandbox,
        patch(
            "agent.server.check_or_recreate_sandbox",
            new_callable=AsyncMock,
            side_effect=passthrough,
        ),
        patch(
            "agent.server._refresh_github_proxy_or_recreate",
            new_callable=AsyncMock,
            side_effect=passthrough,
        ) as refresh_proxy,
        patch("agent.server._configure_git_identity", new_callable=AsyncMock),
        patch("agent.server.client.threads.update", new_callable=AsyncMock) as update_thread,
    ):
        result = await ensure_sandbox_for_thread(thread_id)

    assert result.id == "sandbox-existing"
    connect_sandbox.assert_awaited_once_with("sandbox-existing")
    assert refresh_proxy.await_count == 1
    # Metadata already holds this id, so no update is issued.
    update_thread.assert_not_awaited()
    SANDBOX_BACKENDS.clear()


@pytest.mark.asyncio
async def test_ensure_sandbox_resolves_unresolved_backend_proxy() -> None:
    thread_id = "thread-unresolved-proxy"
    SANDBOX_BACKENDS.clear()
    proxy = get_or_create_sandbox_backend_proxy(thread_id)
    existing_backend = MagicMock()
    existing_backend.id = "sandbox-existing"

    async def passthrough(
        sandbox_backend,
        _thread_id,
        _github_proxy_token=None,
        _github_proxy_repositories=None,
        _repo=None,
    ):
        return sandbox_backend

    with (
        patch(
            "agent.server.get_sandbox_id_from_metadata",
            new_callable=AsyncMock,
            return_value="sandbox-existing",
        ),
        patch(
            "agent.server.create_sandbox",
            new_callable=AsyncMock,
            return_value=existing_backend,
        ) as connect_sandbox,
        patch(
            "agent.server.check_or_recreate_sandbox",
            new_callable=AsyncMock,
            side_effect=passthrough,
        ),
        patch(
            "agent.server._refresh_github_proxy_or_recreate",
            new_callable=AsyncMock,
            side_effect=passthrough,
        ) as refresh_proxy,
        patch("agent.server._configure_git_identity", new_callable=AsyncMock),
        patch("agent.server.client.threads.update", new_callable=AsyncMock) as update_thread,
    ):
        result = await ensure_sandbox_for_thread(thread_id)

    assert result is proxy
    assert proxy.current is existing_backend
    connect_sandbox.assert_awaited_once_with("sandbox-existing")
    assert refresh_proxy.await_count == 1
    update_thread.assert_not_awaited()
    SANDBOX_BACKENDS.clear()
