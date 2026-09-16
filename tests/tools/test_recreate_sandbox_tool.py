from unittest.mock import AsyncMock, patch

import pytest

from agent.tools.recreate_sandbox import recreate_sandbox


@pytest.mark.asyncio
async def test_recreate_sandbox_returns_old_and_new_ids() -> None:
    config = {
        "configurable": {
            "thread_id": "thread-1",
            "repo": {"owner": "langchain-ai", "name": "open-swe"},
        }
    }
    with (
        patch("agent.run_config.get_config", return_value=config),
        patch(
            "agent.sandboxes.lifecycle.recreate_sandbox_for_thread",
            new_callable=AsyncMock,
            return_value=("sandbox-old", "sandbox-new"),
        ) as recreate,
    ):
        result = await recreate_sandbox()

    assert result == {
        "success": True,
        "old_sandbox_id": "sandbox-old",
        "new_sandbox_id": "sandbox-new",
    }
    recreate.assert_awaited_once_with("thread-1", workspace_slug=None, source="workspace")


@pytest.mark.asyncio
async def test_recreate_sandbox_forwards_base_source() -> None:
    config = {"configurable": {"thread_id": "thread-1"}}
    with (
        patch("agent.run_config.get_config", return_value=config),
        patch(
            "agent.sandboxes.lifecycle.recreate_sandbox_for_thread",
            new_callable=AsyncMock,
            return_value=("sandbox-old", "sandbox-new"),
        ) as recreate,
    ):
        result = await recreate_sandbox(source="base")

    assert result["success"] is True
    recreate.assert_awaited_once_with("thread-1", workspace_slug=None, source="base")


@pytest.mark.asyncio
async def test_recreate_sandbox_workspace_overrides_thread_workspace() -> None:
    config = {"configurable": {"thread_id": "thread-1", "workspace": "open-swe"}}
    with (
        patch("agent.run_config.get_config", return_value=config),
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
