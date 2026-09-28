from unittest.mock import AsyncMock, patch

import pytest

from agent.tools.recreate_sandbox import recreate_sandbox


@pytest.mark.asyncio
async def test_recreate_sandbox_workspace_overrides_thread_workspace_for_private_admin() -> None:
    config = {"configurable": {"thread_id": "thread-1", "workspace": "open-swe"}}
    with (
        patch("agent.run_config.get_config", return_value=config),
        patch(
            "agent.tools.recreate_sandbox.require_private_admin_surface",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch(
            "agent.sandboxes.lifecycle.recreate_sandbox_for_thread",
            new_callable=AsyncMock,
            return_value=("sandbox-old", "sandbox-new"),
        ) as recreate,
    ):
        await recreate_sandbox(workspace="langchainplus")

    recreate.assert_awaited_once_with(
        "thread-1", workspace_slug="langchainplus", source="workspace"
    )


@pytest.mark.asyncio
async def test_recreate_sandbox_refuses_other_workspace_outside_private_admin_surface() -> None:
    config = {"configurable": {"thread_id": "thread-1", "workspace": "open-swe"}}
    with (
        patch("agent.run_config.get_config", return_value=config),
        patch(
            "agent.tools.recreate_sandbox.require_private_admin_surface",
            new_callable=AsyncMock,
            return_value="Only workspace admins on a private admin surface can boot another workspace's sandbox image.",
        ),
        patch(
            "agent.sandboxes.lifecycle.recreate_sandbox_for_thread",
            new_callable=AsyncMock,
        ) as recreate,
    ):
        result = await recreate_sandbox(workspace="langchainplus")

    assert result["success"] is False
    assert "private admin surface" in result["error"]
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
