from unittest.mock import AsyncMock, patch

import pytest

from agent.dashboard.team_credentials import LangSmithCredentials
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
    recreate.assert_awaited_once_with("thread-1", environment_slug=None)


@pytest.mark.asyncio
async def test_recreate_sandbox_resolves_email_identity() -> None:
    credentials = LangSmithCredentials("secret", "https://api.smith.langchain.com")
    config = {"configurable": {"thread_id": "thread-1", "user_email": "alice@example.com"}}
    with (
        patch("agent.run_config.get_config", return_value=config),
        patch("agent.dashboard.agent_overrides.resolve_login_from_email", return_value="alice"),
        patch(
            "agent.tools.recreate_sandbox.get_sandbox_langsmith_credentials",
            new_callable=AsyncMock,
            return_value=credentials,
        ) as get_credentials,
        patch(
            "agent.sandboxes.lifecycle.recreate_sandbox_for_thread",
            new_callable=AsyncMock,
            return_value=("old", "new"),
        ) as recreate,
    ):
        await recreate_sandbox()

    get_credentials.assert_awaited_once_with("alice")
    recreate.assert_awaited_once_with(
        "thread-1", environment_slug=None, langsmith_credentials=credentials
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


def test_recreate_sandbox_exported() -> None:
    from agent.tools import recreate_sandbox as exported

    assert exported is recreate_sandbox
