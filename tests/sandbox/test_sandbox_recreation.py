from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.server import recreate_sandbox_for_thread
from agent.utils.sandbox_state import SANDBOX_BACKENDS, set_sandbox_backend


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
            "agent.server.get_sandbox_id_from_metadata",
            new_callable=AsyncMock,
            return_value="sandbox-old",
        ),
        patch(
            "agent.server._create_sandbox_with_proxy",
            new_callable=AsyncMock,
            return_value=new_sandbox,
        ) as create,
        patch("agent.server._configure_git_identity", new_callable=AsyncMock) as configure,
        patch(
            "agent.server.client.threads.update",
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
        environment_slug=None,
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
async def test_recreate_sandbox_keeps_old_binding_when_metadata_update_fails() -> None:
    thread_id = "thread-recreate-failure"
    SANDBOX_BACKENDS.clear()
    old_sandbox = MagicMock(id="sandbox-old")
    new_sandbox = MagicMock(id="sandbox-new")
    proxy = set_sandbox_backend(thread_id, old_sandbox)

    with (
        patch(
            "agent.server.get_sandbox_id_from_metadata",
            new_callable=AsyncMock,
            return_value="sandbox-old",
        ),
        patch(
            "agent.server._create_sandbox_with_proxy",
            new_callable=AsyncMock,
            return_value=new_sandbox,
        ),
        patch("agent.server._configure_git_identity", new_callable=AsyncMock),
        patch(
            "agent.server.client.threads.update",
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
            "agent.server.get_sandbox_id_from_metadata",
            new_callable=AsyncMock,
            return_value="sandbox-same",
        ),
        patch(
            "agent.server._create_sandbox_with_proxy",
            new_callable=AsyncMock,
            return_value=MagicMock(id="sandbox-same"),
        ),
        patch("agent.server._configure_git_identity", new_callable=AsyncMock) as configure,
        patch("agent.server.client.threads.update", new_callable=AsyncMock) as update,
    ):
        with pytest.raises(RuntimeError, match="distinct sandbox"):
            await recreate_sandbox_for_thread(thread_id)

    configure.assert_not_awaited()
    update.assert_not_awaited()
    assert SANDBOX_BACKENDS[thread_id].current is old_sandbox
    SANDBOX_BACKENDS.clear()
