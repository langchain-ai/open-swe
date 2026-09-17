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
async def test_recreate_sandbox_allows_other_workspace_from_admin_slack_dm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CONFIGURED_ADMINS", "admin")
    config = {
        "configurable": {
            "thread_id": "thread-1",
            "workspace": "open-swe",
            "admin_thread": True,
            "source": "slack",
            "github_login": "admin",
            "slack_thread": {
                "channel_id": "D123",
                "thread_ts": "0",
                "channel_context": {"is_im": True},
            },
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
        result = await recreate_sandbox(workspace="langchainplus")

    assert result["success"] is True
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
async def test_recreate_sandbox_own_workspace_needs_no_admin() -> None:
    config = {"configurable": {"thread_id": "thread-1", "workspace": "open-swe"}}
    with (
        patch("agent.run_config.get_config", return_value=config),
        patch(
            "agent.tools.recreate_sandbox.require_private_admin_surface",
            new_callable=AsyncMock,
        ) as gate,
        patch(
            "agent.sandboxes.lifecycle.recreate_sandbox_for_thread",
            new_callable=AsyncMock,
            return_value=("sandbox-old", "sandbox-new"),
        ),
    ):
        result = await recreate_sandbox(workspace="open-swe")

    assert result["success"] is True
    gate.assert_not_awaited()


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
