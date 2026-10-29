"""Connecting to a thread's sandbox, which the backend never creates."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openswe.sandboxes.lifecycle import SANDBOX_BACKENDS, ensure_sandbox_for_thread
from openswe.sandboxes.state import (
    SANDBOX_CONNECTIONS,
    SandboxUnreachableError,
    get_or_create_sandbox_backend_proxy,
    set_sandbox_backend,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("owner", [None, "mda"])
async def test_ensure_sandbox_never_creates_one_for_a_thread_without(owner: str | None) -> None:
    """MDA creates every thread's box: a thread without one waits for its run to open it."""
    thread_id = "thread-new"
    SANDBOX_BACKENDS.clear()
    metadata = {} if owner is None else {"sandbox_owner": owner}

    with (
        patch(
            "openswe.sandboxes.lifecycle.get_sandbox_metadata",
            new_callable=AsyncMock,
            return_value=metadata,
        ),
        patch(
            "openswe.sandboxes.lifecycle.connect_existing_sandbox", new_callable=AsyncMock
        ) as connect,
        patch(
            "openswe.sandboxes.lifecycle.client.threads.update", new_callable=AsyncMock
        ) as update_thread,
        pytest.raises(SandboxUnreachableError, match="Managed Deep Agents"),
    ):
        await ensure_sandbox_for_thread(thread_id)

    connect.assert_not_awaited()
    update_thread.assert_not_awaited()
    assert thread_id not in SANDBOX_BACKENDS


@pytest.mark.asyncio
async def test_ensure_sandbox_reconnects_to_metadata_sandbox() -> None:
    thread_id = "thread-reconnect"
    SANDBOX_BACKENDS.clear()
    existing_backend = MagicMock()
    existing_backend.id = "sandbox-existing"

    async def passthrough(
        sandbox_backend,
        _thread_id,
        _github_proxy_repositories=None,
        _base_proxy_config=None,
        _workspace_slug=None,
        *,
        github_proxy_permissions=None,
    ):
        return sandbox_backend

    with (
        patch(
            "openswe.sandboxes.lifecycle.get_sandbox_metadata",
            new_callable=AsyncMock,
            return_value={"sandbox_id": "sandbox-existing"},
        ),
        patch(
            "openswe.sandboxes.lifecycle.connect_existing_sandbox",
            new_callable=AsyncMock,
            return_value=existing_backend,
        ) as connect_sandbox,
        patch(
            "openswe.sandboxes.lifecycle._refresh_github_proxy_or_fail",
            new_callable=AsyncMock,
            side_effect=passthrough,
        ) as refresh_proxy,
        patch("openswe.sandboxes.lifecycle.configure_git_identity", new_callable=AsyncMock),
        patch(
            "openswe.sandboxes.lifecycle.client.threads.update", new_callable=AsyncMock
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
        _github_proxy_repositories=None,
        _base_proxy_config=None,
        _workspace_slug=None,
        *,
        github_proxy_permissions=None,
    ):
        return sandbox_backend

    with (
        patch(
            "openswe.sandboxes.lifecycle.get_sandbox_metadata",
            new_callable=AsyncMock,
            return_value={"sandbox_id": "sandbox-existing"},
        ),
        patch(
            "openswe.sandboxes.lifecycle.connect_existing_sandbox",
            new_callable=AsyncMock,
            return_value=existing_backend,
        ) as connect_sandbox,
        patch(
            "openswe.sandboxes.lifecycle._refresh_github_proxy_or_fail",
            new_callable=AsyncMock,
            side_effect=passthrough,
        ) as refresh_proxy,
        patch("openswe.sandboxes.lifecycle.configure_git_identity", new_callable=AsyncMock),
        patch(
            "openswe.sandboxes.lifecycle.client.threads.update", new_callable=AsyncMock
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
        _github_proxy_repositories=None,
        _base_proxy_config=None,
        _workspace_slug=None,
        *,
        github_proxy_permissions=None,
    ):
        return sandbox_backend

    with (
        patch(
            "openswe.sandboxes.lifecycle.get_sandbox_metadata",
            new_callable=AsyncMock,
            return_value={"sandbox_id": "sandbox-new"},
        ),
        patch(
            "openswe.sandboxes.lifecycle.connect_existing_sandbox",
            new_callable=AsyncMock,
            return_value=new_backend,
        ) as connect_sandbox,
        patch(
            "openswe.sandboxes.lifecycle._refresh_github_proxy_or_fail",
            new_callable=AsyncMock,
            side_effect=passthrough,
        ),
        patch("openswe.sandboxes.lifecycle.configure_git_identity", new_callable=AsyncMock),
        patch(
            "openswe.sandboxes.lifecycle.client.threads.update", new_callable=AsyncMock
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
    """A failed lookup is not an unbound thread; unbinding here would strand its real sandbox."""
    thread_id = "thread-metadata-down"
    set_sandbox_backend(thread_id, MagicMock(id="sandbox-live"))

    with (
        patch(
            "openswe.sandboxes.lifecycle.get_sandbox_metadata",
            new_callable=AsyncMock,
            side_effect=RuntimeError("langgraph api unavailable"),
        ),
        patch(
            "openswe.sandboxes.lifecycle.client.threads.update", new_callable=AsyncMock
        ) as update_thread,
    ):
        with pytest.raises(RuntimeError, match="langgraph api unavailable"):
            await ensure_sandbox_for_thread(thread_id)

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
