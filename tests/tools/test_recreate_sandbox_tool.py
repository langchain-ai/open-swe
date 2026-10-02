from collections.abc import Callable
from unittest.mock import AsyncMock, patch

import pytest

from agent.tools.recreate_sandbox import recreate_sandbox


@pytest.mark.asyncio
@pytest.mark.parametrize("stop_error", [None, "stop unavailable"])
async def test_recreate_sandbox_workspace_overrides_thread_workspace_for_private_admin(
    grant_tool_access: Callable[..., None],
    stop_error: str | None,
) -> None:
    grant_tool_access(admin=True, admin_surface=True)
    config = {"configurable": {"thread_id": "thread-1", "workspace": "open-swe"}}
    with (
        patch("agent.run_config.get_config", return_value=config),
        patch(
            "agent.sandboxes.lifecycle.recreate_sandbox_for_thread",
            new_callable=AsyncMock,
            return_value=("sandbox-old", "sandbox-new", stop_error),
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
        patch("agent.run_config.get_config", return_value=config),
        patch(
            "agent.sandboxes.lifecycle.recreate_sandbox_for_thread",
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
        patch("agent.run_config.get_config", return_value=config),
        patch(
            "agent.sandboxes.lifecycle.recreate_sandbox_for_thread",
            new_callable=AsyncMock,
            side_effect=RuntimeError("creation failed"),
        ),
    ):
        result = await recreate_sandbox()

    assert result == {"success": False, "error": "creation failed"}
