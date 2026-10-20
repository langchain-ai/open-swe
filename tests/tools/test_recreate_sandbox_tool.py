from collections.abc import Callable
from unittest.mock import AsyncMock, patch

import pytest

from openswe.sandboxes.lifecycle import SandboxRecreationStopError
from openswe.tools.recreate_sandbox import recreate_sandbox


@pytest.fixture(autouse=True)
def _backend_owned_sandbox() -> object:
    """A thread whose box the backend made, as every thread had before MDA owned them."""
    with patch(
        "openswe.tools.recreate_sandbox.get_sandbox_metadata",
        new_callable=AsyncMock,
        return_value={"sandbox_id": "sandbox-old"},
    ) as metadata:
        yield metadata


@pytest.mark.asyncio
async def test_recreate_sandbox_leaves_an_mda_box_to_mda(
    grant_tool_access: Callable[..., None], _backend_owned_sandbox: AsyncMock
) -> None:
    """MDA replaces its own box; the backend answers what with, and never makes one itself."""
    grant_tool_access()
    _backend_owned_sandbox.return_value = {"sandbox_id": "mda-box", "sandbox_owner": "mda"}
    config = {"configurable": {"thread_id": "thread-1", "workspace": "open-swe"}}
    with (
        patch("openswe.run_config.get_config", return_value=config),
        patch(
            "openswe.sandboxes.lifecycle.load_workspace", new_callable=AsyncMock, return_value=None
        ),
        patch(
            "openswe.sandboxes.lifecycle.recreate_sandbox_for_thread", new_callable=AsyncMock
        ) as recreate,
        patch(
            "openswe.sandboxes.lifecycle.client.threads.update", new_callable=AsyncMock
        ) as update,
    ):
        result = await recreate_sandbox(source="base")

    assert result["success"] is True
    assert result["replace"]["snapshot_id"] is None
    recreate.assert_not_awaited()
    # The reconnect takes the replacement's proxy from this record, never from MDA.
    update.assert_awaited_once_with(
        thread_id="thread-1",
        metadata={"sandbox_replacement": {"workspace": "open-swe", "source": "base"}},
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("stop_error", [None, "stop unavailable"])
async def test_recreate_sandbox_workspace_overrides_thread_workspace_for_private_admin(
    grant_tool_access: Callable[..., None],
    stop_error: str | None,
) -> None:
    grant_tool_access(admin=True, admin_surface=True)
    config = {"configurable": {"thread_id": "thread-1", "workspace": "open-swe"}}
    with (
        patch("openswe.run_config.get_config", return_value=config),
        patch(
            "openswe.sandboxes.lifecycle.recreate_sandbox_for_thread",
            new_callable=AsyncMock,
            return_value=("sandbox-old", "sandbox-new"),
            side_effect=(
                SandboxRecreationStopError("sandbox-old", "sandbox-new", stop_error)
                if stop_error is not None
                else None
            ),
        ) as recreate,
    ):
        result = await recreate_sandbox(workspace="langchainplus")

    assert result == {
        "success": True,
        "old_sandbox_id": "sandbox-old",
        "new_sandbox_id": "sandbox-new",
        "old_sandbox_stopped": stop_error is None,
        "old_sandbox_stop_error": stop_error,
    }
    recreate.assert_awaited_once_with(
        "thread-1", workspace_slug="langchainplus", source="workspace"
    )


@pytest.mark.asyncio
async def test_recreate_sandbox_refuses_other_workspace_outside_private_admin_surface(
    grant_tool_access: Callable[..., None],
) -> None:
    grant_tool_access(admin=True, admin_thread=True)
    config = {"configurable": {"thread_id": "thread-1", "workspace": "open-swe"}}
    with (
        patch("openswe.run_config.get_config", return_value=config),
        patch(
            "openswe.sandboxes.lifecycle.recreate_sandbox_for_thread",
            new_callable=AsyncMock,
        ) as recreate,
    ):
        result = await recreate_sandbox(workspace="langchainplus")

    assert "not available in this thread" in str(result["error"])
    recreate.assert_not_awaited()


@pytest.mark.asyncio
async def test_recreate_sandbox_reports_failure_without_ids() -> None:
    config = {"configurable": {"thread_id": "thread-1"}}

    with (
        patch("openswe.run_config.get_config", return_value=config),
        patch(
            "openswe.sandboxes.lifecycle.recreate_sandbox_for_thread",
            new_callable=AsyncMock,
            side_effect=RuntimeError("creation failed"),
        ),
    ):
        result = await recreate_sandbox()

    assert result == {"success": False, "error": "creation failed"}
