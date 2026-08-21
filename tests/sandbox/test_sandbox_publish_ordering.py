"""A sandbox becomes reachable through the cache only once it is initialized.

The startup task publishes the backend it built, and callers read that cached
backend without awaiting the task. Publishing before initialization finishes
would hand the rest of the run a sandbox whose setup failed, with the failure
visible only in the task's done callback.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.runtime.sandbox import ensure_sandbox_for_thread
from agent.utils.sandbox_registry import SANDBOX_BACKENDS, get_or_create_sandbox_backend_proxy


@pytest.mark.asyncio
@pytest.mark.parametrize("failing_step", ["_configure_git_identity", "client.threads.update"])
async def test_initialization_failure_publishes_nothing(failing_step: str) -> None:
    thread_id = "thread-init-fails"
    proxy = get_or_create_sandbox_backend_proxy(thread_id)
    created = MagicMock()
    created.id = "sandbox-new"

    steps = {
        "_configure_git_identity": AsyncMock(),
        "client.threads.update": AsyncMock(),
    }
    steps[failing_step].side_effect = RuntimeError("initialization failed")

    with (
        patch(
            "agent.runtime.sandbox.get_sandbox_id_from_metadata",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch(
            "agent.runtime.sandbox._create_sandbox_with_proxy",
            new_callable=AsyncMock,
            return_value=created,
        ),
        patch("agent.runtime.sandbox._configure_git_identity", steps["_configure_git_identity"]),
        patch("agent.runtime.sandbox.client.threads.update", steps["client.threads.update"]),
        pytest.raises(RuntimeError, match="initialization failed"),
    ):
        await ensure_sandbox_for_thread(thread_id)

    # A later ready() must await the startup task and see the failure, not take
    # the cached-backend fast path.
    assert not proxy.has_backend
    assert not SANDBOX_BACKENDS[thread_id].has_backend
