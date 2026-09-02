from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.sandboxes.lifecycle import reset_sandbox_for_thread
from agent.sandboxes.state import SANDBOX_BACKENDS, set_sandbox_backend


@pytest.mark.asyncio
async def test_reset_sandbox_hands_off_after_metadata_persists() -> None:
    thread_id = "thread-reset"
    create_params: dict[str, Any] = {
        "snapshot_name": "python:latest",
        "cpu_millicores": 500,
        "_internal_runtime": "v2",
        "proxy_config": {"rules": []},
    }
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
            "agent.sandboxes.lifecycle.create_langsmith_sandbox_from_params",
            new_callable=AsyncMock,
            return_value=new_sandbox,
        ) as create,
        patch(
            "agent.sandboxes.lifecycle._resolve_proxy_token",
            new_callable=AsyncMock,
            return_value=("ghs_install", "expiry", None),
        ),
        patch(
            "agent.sandboxes.lifecycle._configure_github_proxy", new_callable=AsyncMock
        ) as proxy_configure,
        patch("agent.sandboxes.lifecycle.record_proxy_token_expiry") as record_proxy,
        patch(
            "agent.sandboxes.lifecycle._configure_git_identity", new_callable=AsyncMock
        ) as configure,
        patch(
            "agent.sandboxes.lifecycle.client.threads.update",
            new_callable=AsyncMock,
            side_effect=persist_metadata,
        ) as update,
    ):
        result = await reset_sandbox_for_thread(thread_id, create_params)

    assert result == ("sandbox-old", "sandbox-new")
    create.assert_awaited_once_with(create_params)
    proxy_configure.assert_awaited_once_with(
        "sandbox-new", "ghs_install", base_proxy_config={"rules": []}
    )
    record_proxy.assert_called_once_with(
        thread_id,
        "expiry",
        permissions=None,
        base_proxy_config={"rules": []},
    )
    configure.assert_awaited_once_with(new_sandbox)
    update.assert_awaited_once_with(
        thread_id=thread_id,
        metadata={
            "sandbox_id": "sandbox-new",
            "sandbox_base_proxy_config": {"rules": []},
        },
    )
    assert proxy.current is new_sandbox
    SANDBOX_BACKENDS.clear()


@pytest.mark.asyncio
async def test_reset_sandbox_clears_stale_proxy_metadata() -> None:
    thread_id = "thread-reset-default-proxy"
    SANDBOX_BACKENDS.clear()
    old_sandbox = MagicMock(id="sandbox-old")
    new_sandbox = MagicMock(id="sandbox-new")
    set_sandbox_backend(thread_id, old_sandbox)

    with (
        patch(
            "agent.sandboxes.lifecycle.get_sandbox_id_from_metadata",
            new_callable=AsyncMock,
            return_value="sandbox-old",
        ),
        patch(
            "agent.sandboxes.lifecycle.create_langsmith_sandbox_from_params",
            new_callable=AsyncMock,
            return_value=new_sandbox,
        ),
        patch(
            "agent.sandboxes.lifecycle._resolve_proxy_token",
            new_callable=AsyncMock,
            return_value=("ghs_install", "expiry", None),
        ),
        patch("agent.sandboxes.lifecycle._configure_github_proxy", new_callable=AsyncMock),
        patch("agent.sandboxes.lifecycle.record_proxy_token_expiry"),
        patch("agent.sandboxes.lifecycle._configure_git_identity", new_callable=AsyncMock),
        patch("agent.sandboxes.lifecycle.client.threads.update", new_callable=AsyncMock) as update,
    ):
        await reset_sandbox_for_thread(thread_id, {})

    update.assert_awaited_once_with(
        thread_id=thread_id,
        metadata={"sandbox_id": "sandbox-new", "sandbox_base_proxy_config": None},
    )
    SANDBOX_BACKENDS.clear()


@pytest.mark.asyncio
async def test_reset_sandbox_does_not_record_proxy_before_metadata_persists() -> None:
    thread_id = "thread-reset-failure"
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
            "agent.sandboxes.lifecycle.create_langsmith_sandbox_from_params",
            new_callable=AsyncMock,
            return_value=new_sandbox,
        ),
        patch(
            "agent.sandboxes.lifecycle._resolve_proxy_token",
            new_callable=AsyncMock,
            return_value=("ghs_install", "expiry", None),
        ),
        patch("agent.sandboxes.lifecycle._configure_github_proxy", new_callable=AsyncMock),
        patch("agent.sandboxes.lifecycle.record_proxy_token_expiry") as record_proxy,
        patch("agent.sandboxes.lifecycle._configure_git_identity", new_callable=AsyncMock),
        patch(
            "agent.sandboxes.lifecycle.client.threads.update",
            new_callable=AsyncMock,
            side_effect=RuntimeError("metadata unavailable"),
        ),
    ):
        with pytest.raises(RuntimeError, match="metadata unavailable"):
            await reset_sandbox_for_thread(thread_id, {"proxy_config": {"rules": []}})

    record_proxy.assert_not_called()
    assert proxy.current is old_sandbox
    SANDBOX_BACKENDS.clear()


@pytest.mark.asyncio
async def test_reset_sandbox_rejects_other_providers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SANDBOX_TYPE", "modal")

    with pytest.raises(ValueError, match="only supported by the LangSmith"):
        await reset_sandbox_for_thread("thread-reset", {})
