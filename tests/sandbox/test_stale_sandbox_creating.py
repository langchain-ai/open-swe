"""Simplified sandbox get-or-create-then-reconnect flow (no ``__creating__`` sentinel).

Dispatch uses ``multitask_strategy="interrupt"`` so a thread never provisions
two sandboxes concurrently; the cross-process sentinel poll was removed.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.sandboxes.lifecycle import SANDBOX_BACKENDS, ensure_sandbox_for_thread
from agent.sandboxes.state import (
    SANDBOX_CONNECTIONS,
    get_or_create_sandbox_backend_proxy,
    set_sandbox_backend,
)


@pytest.mark.asyncio
async def test_ensure_sandbox_creates_new_when_no_metadata() -> None:
    thread_id = "thread-new"
    SANDBOX_BACKENDS.clear()
    sandbox_backend = MagicMock()
    sandbox_backend.id = "sandbox-new"

    with (
        patch(
            "agent.sandboxes.lifecycle.get_sandbox_metadata",
            new_callable=AsyncMock,
            return_value={},
        ),
        patch(
            "agent.sandboxes.lifecycle._create_sandbox_with_proxy",
            new_callable=AsyncMock,
            return_value=sandbox_backend,
        ) as create_sandbox,
        patch("agent.sandboxes.lifecycle.configure_git_identity", new_callable=AsyncMock),
        patch(
            "agent.sandboxes.lifecycle.client.threads.update", new_callable=AsyncMock
        ) as update_thread,
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
        _base_proxy_config=None,
    ):
        return sandbox_backend

    with (
        patch(
            "agent.sandboxes.lifecycle.get_sandbox_metadata",
            new_callable=AsyncMock,
            return_value={"sandbox_id": "sandbox-existing"},
        ),
        patch(
            "agent.sandboxes.lifecycle.create_sandbox",
            new_callable=AsyncMock,
            return_value=existing_backend,
        ) as connect_sandbox,
        patch(
            "agent.sandboxes.lifecycle._refresh_github_proxy_or_fail",
            new_callable=AsyncMock,
            side_effect=passthrough,
        ) as refresh_proxy,
        patch("agent.sandboxes.lifecycle.configure_git_identity", new_callable=AsyncMock),
        patch(
            "agent.sandboxes.lifecycle.client.threads.update", new_callable=AsyncMock
        ) as update_thread,
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
        _base_proxy_config=None,
    ):
        return sandbox_backend

    with (
        patch(
            "agent.sandboxes.lifecycle.get_sandbox_metadata",
            new_callable=AsyncMock,
            return_value={"sandbox_id": "sandbox-existing"},
        ),
        patch(
            "agent.sandboxes.lifecycle.create_sandbox",
            new_callable=AsyncMock,
            return_value=existing_backend,
        ) as connect_sandbox,
        patch(
            "agent.sandboxes.lifecycle._refresh_github_proxy_or_fail",
            new_callable=AsyncMock,
            side_effect=passthrough,
        ) as refresh_proxy,
        patch("agent.sandboxes.lifecycle.configure_git_identity", new_callable=AsyncMock),
        patch(
            "agent.sandboxes.lifecycle.client.threads.update", new_callable=AsyncMock
        ) as update_thread,
    ):
        result = await ensure_sandbox_for_thread(thread_id)

    assert result is proxy
    assert proxy.current is existing_backend
    connect_sandbox.assert_awaited_once_with("sandbox-existing")
    assert refresh_proxy.await_count == 1
    update_thread.assert_not_awaited()
    SANDBOX_BACKENDS.clear()


@pytest.mark.asyncio
async def test_ensure_sandbox_never_reuses_connection_to_another_sandbox() -> None:
    """A reset on another worker rebinds the thread; this worker still holds the old box."""
    thread_id = "thread-stale-cache"
    SANDBOX_BACKENDS.clear()
    stale_backend = MagicMock()
    stale_backend.id = "sandbox-old"
    proxy = set_sandbox_backend(thread_id, stale_backend)
    new_backend = MagicMock()
    new_backend.id = "sandbox-new"

    async def passthrough(
        sandbox_backend,
        _thread_id,
        _github_proxy_token=None,
        _github_proxy_repositories=None,
        _base_proxy_config=None,
    ):
        return sandbox_backend

    with (
        patch(
            "agent.sandboxes.lifecycle.get_sandbox_metadata",
            new_callable=AsyncMock,
            return_value={"sandbox_id": "sandbox-new"},
        ),
        patch(
            "agent.sandboxes.lifecycle.create_sandbox",
            new_callable=AsyncMock,
            return_value=new_backend,
        ) as connect_sandbox,
        patch(
            "agent.sandboxes.lifecycle._refresh_github_proxy_or_fail",
            new_callable=AsyncMock,
            side_effect=passthrough,
        ),
        patch("agent.sandboxes.lifecycle.configure_git_identity", new_callable=AsyncMock),
        patch(
            "agent.sandboxes.lifecycle.client.threads.update", new_callable=AsyncMock
        ) as update_thread,
    ):
        result = await ensure_sandbox_for_thread(thread_id)

    assert result is proxy
    assert proxy.current is new_backend
    connect_sandbox.assert_awaited_once_with("sandbox-new")
    # Only a sandbox created in this call binds the thread; reconnecting never does.
    update_thread.assert_not_awaited()
    assert "sandbox-old" not in SANDBOX_CONNECTIONS
    assert SANDBOX_CONNECTIONS["sandbox-new"] is new_backend
    SANDBOX_BACKENDS.clear()


@pytest.mark.asyncio
async def test_ensure_sandbox_does_not_replace_sandbox_when_metadata_lookup_fails() -> None:
    """A failed lookup is not an unbound thread; creating here would strand its real sandbox."""
    thread_id = "thread-metadata-down"
    set_sandbox_backend(thread_id, MagicMock(id="sandbox-live"))

    with (
        patch(
            "agent.sandboxes.lifecycle.get_sandbox_metadata",
            new_callable=AsyncMock,
            side_effect=RuntimeError("langgraph api unavailable"),
        ),
        patch(
            "agent.sandboxes.lifecycle._create_sandbox_with_proxy", new_callable=AsyncMock
        ) as create,
        patch(
            "agent.sandboxes.lifecycle.client.threads.update", new_callable=AsyncMock
        ) as update_thread,
    ):
        with pytest.raises(RuntimeError, match="langgraph api unavailable"):
            await ensure_sandbox_for_thread(thread_id)

    create.assert_not_awaited()
    update_thread.assert_not_awaited()
    assert SANDBOX_CONNECTIONS["sandbox-live"] is not None


def test_set_sandbox_backend_drops_connection_to_the_previous_sandbox() -> None:
    thread_id = "thread-move"
    old = MagicMock(id="sandbox-old")
    new = MagicMock(id="sandbox-new")

    set_sandbox_backend(thread_id, old)
    assert SANDBOX_CONNECTIONS["sandbox-old"] is old

    set_sandbox_backend(thread_id, new)
    assert "sandbox-old" not in SANDBOX_CONNECTIONS
    assert SANDBOX_CONNECTIONS["sandbox-new"] is new
