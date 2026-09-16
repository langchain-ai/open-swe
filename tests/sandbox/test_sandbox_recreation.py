from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.sandboxes.lifecycle import recreate_sandbox_for_thread
from agent.sandboxes.state import SANDBOX_BACKENDS, set_sandbox_backend


@pytest.mark.asyncio
async def test_recreate_sandbox_hands_off_after_metadata_persists() -> None:
    thread_id = "thread-recreate"
    SANDBOX_BACKENDS.clear()
    old_sandbox = MagicMock(id="sandbox-old")
    new_sandbox = MagicMock(id="sandbox-new")
    proxy = set_sandbox_backend(thread_id, old_sandbox)

    async def persist_metadata(**_kwargs: object) -> None:
        assert proxy.current is old_sandbox

    with (
        patch(
            "agent.sandboxes.lifecycle.get_sandbox_id_from_metadata",
            new_callable=AsyncMock,
            return_value="sandbox-old",
        ),
        patch(
            "agent.sandboxes.lifecycle._create_sandbox_with_proxy",
            new_callable=AsyncMock,
            return_value=new_sandbox,
        ) as create,
        patch(
            "agent.sandboxes.lifecycle.configure_git_identity", new_callable=AsyncMock
        ) as configure,
        patch(
            "agent.sandboxes.lifecycle.client.threads.update",
            new_callable=AsyncMock,
            side_effect=persist_metadata,
        ) as update,
    ):
        result = await recreate_sandbox_for_thread(
            thread_id,
        )

    assert result == ("sandbox-old", "sandbox-new")
    create.assert_awaited_once_with(
        thread_id=thread_id,
        workspace_slug=None,
        source="workspace",
    )
    configure.assert_awaited_once_with(new_sandbox)
    update.assert_awaited_once_with(
        thread_id=thread_id,
        metadata={"sandbox_id": "sandbox-new"},
    )
    assert SANDBOX_BACKENDS[thread_id] is proxy
    assert proxy.current is new_sandbox
    SANDBOX_BACKENDS.clear()


@pytest.mark.asyncio
async def test_recreate_sandbox_base_source_skips_workspace_snapshot() -> None:
    thread_id = "thread-recreate-base"
    SANDBOX_BACKENDS.clear()
    set_sandbox_backend(thread_id, MagicMock(id="sandbox-old"))

    with (
        patch(
            "agent.sandboxes.lifecycle.get_sandbox_id_from_metadata",
            new_callable=AsyncMock,
            return_value="sandbox-old",
        ),
        patch(
            "agent.sandboxes.lifecycle._create_sandbox_with_proxy",
            new_callable=AsyncMock,
            return_value=MagicMock(id="sandbox-new"),
        ) as create,
        patch("agent.sandboxes.lifecycle.configure_git_identity", new_callable=AsyncMock),
        patch("agent.sandboxes.lifecycle.client.threads.update", new_callable=AsyncMock),
    ):
        result = await recreate_sandbox_for_thread(
            thread_id, workspace_slug="langchainplus", source="base"
        )

    assert result == ("sandbox-old", "sandbox-new")
    create.assert_awaited_once_with(
        thread_id=thread_id, workspace_slug="langchainplus", source="base"
    )
    SANDBOX_BACKENDS.clear()


@pytest.mark.asyncio
async def test_base_source_skips_workspace_lookup_entirely() -> None:
    """An absent slug still resolves the `default` workspace, so base must not rely on it."""
    from agent.sandboxes.lifecycle import SandboxCreateConfig

    with (
        patch("agent.sandboxes.lifecycle.load_workspace", new_callable=AsyncMock) as load_workspace,
        patch(
            "agent.sandboxes.lifecycle.get_admin_base_snapshot_id",
            new_callable=AsyncMock,
            return_value="snapshot-base",
        ),
    ):
        config = await SandboxCreateConfig.resolve("langchainplus", source="base")

    load_workspace.assert_not_awaited()
    assert config.snapshot_id == "snapshot-base"
    assert config.workspace is None
    assert config.create_params == {}


@pytest.mark.asyncio
async def test_recreate_sandbox_keeps_old_binding_when_metadata_update_fails() -> None:
    thread_id = "thread-recreate-failure"
    SANDBOX_BACKENDS.clear()
    old_sandbox = MagicMock(id="sandbox-old")
    new_sandbox = MagicMock(id="sandbox-new")
    proxy = set_sandbox_backend(thread_id, old_sandbox)

    with (
        patch(
            "agent.sandboxes.lifecycle.get_sandbox_id_from_metadata",
            new_callable=AsyncMock,
            return_value="sandbox-old",
        ),
        patch(
            "agent.sandboxes.lifecycle._create_sandbox_with_proxy",
            new_callable=AsyncMock,
            return_value=new_sandbox,
        ),
        patch("agent.sandboxes.lifecycle.configure_git_identity", new_callable=AsyncMock),
        patch(
            "agent.sandboxes.lifecycle.client.threads.update",
            new_callable=AsyncMock,
            side_effect=RuntimeError("metadata unavailable"),
        ),
    ):
        with pytest.raises(RuntimeError, match="metadata unavailable"):
            await recreate_sandbox_for_thread(thread_id)

    assert SANDBOX_BACKENDS[thread_id] is proxy
    assert proxy.current is old_sandbox
    SANDBOX_BACKENDS.clear()


@pytest.mark.asyncio
async def test_recreate_sandbox_rejects_non_distinct_provider_result() -> None:
    thread_id = "thread-recreate-same-id"
    SANDBOX_BACKENDS.clear()
    old_sandbox = MagicMock(id="sandbox-same")
    set_sandbox_backend(thread_id, old_sandbox)

    with (
        patch(
            "agent.sandboxes.lifecycle.get_sandbox_id_from_metadata",
            new_callable=AsyncMock,
            return_value="sandbox-same",
        ),
        patch(
            "agent.sandboxes.lifecycle._create_sandbox_with_proxy",
            new_callable=AsyncMock,
            return_value=MagicMock(id="sandbox-same"),
        ),
        patch(
            "agent.sandboxes.lifecycle.configure_git_identity", new_callable=AsyncMock
        ) as configure,
        patch("agent.sandboxes.lifecycle.client.threads.update", new_callable=AsyncMock) as update,
    ):
        with pytest.raises(RuntimeError, match="distinct sandbox"):
            await recreate_sandbox_for_thread(thread_id)

    configure.assert_not_awaited()
    update.assert_not_awaited()
    assert SANDBOX_BACKENDS[thread_id].current is old_sandbox
    SANDBOX_BACKENDS.clear()
