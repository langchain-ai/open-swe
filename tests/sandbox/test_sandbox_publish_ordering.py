"""A sandbox becomes reachable through the cache only once it is initialized.

The startup task publishes the backend it built, and callers read that cached
backend without awaiting the task. Publishing before initialization finishes
would hand the rest of the run a sandbox whose setup failed, with the failure
visible only in the task's done callback.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.sandboxes.state import get_or_create_sandbox_backend_proxy
from agent.server import SANDBOX_BACKENDS, ensure_sandbox_for_thread


@pytest.mark.asyncio
@pytest.mark.parametrize("failing_step", ["_create_sandbox_with_proxy", "client.threads.update"])
async def test_initialization_failure_publishes_nothing(failing_step: str) -> None:
    thread_id = "thread-init-fails"
    SANDBOX_BACKENDS.clear()
    proxy = get_or_create_sandbox_backend_proxy(thread_id)
    created = MagicMock()
    created.id = "sandbox-new"

    steps = {
        "_create_sandbox_with_proxy": AsyncMock(return_value=created),
        "client.threads.update": AsyncMock(),
    }
    steps[failing_step].side_effect = RuntimeError("initialization failed")

    with (
        patch(
            "agent.server.get_sandbox_id_from_metadata",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch("agent.server._create_sandbox_with_proxy", steps["_create_sandbox_with_proxy"]),
        patch("agent.server.client.threads.update", steps["client.threads.update"]),
        pytest.raises(RuntimeError, match="initialization failed"),
    ):
        await ensure_sandbox_for_thread(thread_id)

    # A later ready() must await the startup task and see the failure, not take
    # the cached-backend fast path.
    assert not proxy.has_backend
    assert not SANDBOX_BACKENDS[thread_id].has_backend
    SANDBOX_BACKENDS.clear()


@pytest.mark.asyncio
async def test_new_sandbox_persists_its_base_proxy_config() -> None:
    thread_id = "thread-proxy-config"
    SANDBOX_BACKENDS.clear()
    created = MagicMock(id="sandbox-new")
    base_proxy_config = {"rules": [{"name": "public-api", "match_hosts": ["example.com"]}]}
    update = AsyncMock()

    with (
        patch(
            "agent.server.get_sandbox_id_from_metadata",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch(
            "agent.server._create_sandbox_with_proxy",
            new_callable=AsyncMock,
            return_value=created,
        ),
        patch(
            "agent.server.get_recorded_proxy_base_config",
            return_value=base_proxy_config,
        ),
        patch("agent.server._configure_git_identity", new_callable=AsyncMock),
        patch("agent.server.client.threads.update", update),
    ):
        await ensure_sandbox_for_thread(thread_id)

    update.assert_awaited_once_with(
        thread_id=thread_id,
        metadata={
            "sandbox_id": "sandbox-new",
            "sandbox_base_proxy_config": base_proxy_config,
        },
    )
    SANDBOX_BACKENDS.clear()
