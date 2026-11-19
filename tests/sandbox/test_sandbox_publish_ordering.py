"""A sandbox becomes reachable through the cache only once it is initialized.

The startup task publishes the backend it connected to, and callers read that
cached backend without awaiting the task. Publishing before initialization
finishes would hand the rest of the run a sandbox whose setup failed, with the
failure visible only in the task's done callback.
"""

from unittest.mock import AsyncMock, patch

import pytest

from openswe.sandboxes.lifecycle import SANDBOX_BACKENDS, ensure_sandbox_for_thread
from openswe.sandboxes.state import get_or_create_sandbox_backend_proxy


@pytest.mark.asyncio
async def test_initialization_failure_publishes_nothing() -> None:
    thread_id = "thread-init-fails"
    SANDBOX_BACKENDS.clear()
    proxy = get_or_create_sandbox_backend_proxy(thread_id)

    with (
        patch(
            "openswe.sandboxes.lifecycle.get_sandbox_metadata",
            new_callable=AsyncMock,
            return_value={"sandbox_id": "sandbox-mda", "sandbox_owner": "mda"},
        ),
        patch(
            "openswe.sandboxes.lifecycle.connect_existing_sandbox",
            AsyncMock(side_effect=RuntimeError("initialization failed")),
        ),
        patch("openswe.sandboxes.lifecycle._refresh_github_proxy", new_callable=AsyncMock),
        patch("openswe.sandboxes.lifecycle.configure_git_identity", new_callable=AsyncMock),
        patch("openswe.sandboxes.lifecycle.client.threads.update", new_callable=AsyncMock),
        pytest.raises(Exception, match="initialization failed"),
    ):
        await ensure_sandbox_for_thread(thread_id)

    # A later ready() must await the startup task and see the failure, not take
    # the cached-backend fast path.
    assert not proxy.has_backend
    assert not SANDBOX_BACKENDS[thread_id].has_backend
    SANDBOX_BACKENDS.clear()
