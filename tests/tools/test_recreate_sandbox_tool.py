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
    repo = {"owner": "langchain-ai", "name": "open-swe"}

    with (
        patch("agent.tools.recreate_sandbox.get_config", return_value=config),
        patch(
            "agent.tools.recreate_sandbox.resolve_default_repo",
            new_callable=AsyncMock,
            return_value=repo,
        ) as resolve_repo,
        patch(
            "agent.tools.recreate_sandbox.recreate_sandbox_for_thread",
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
    resolve_repo.assert_awaited_once_with(config["configurable"])
    recreate.assert_awaited_once_with("thread-1", repo=repo, environment_slug=None)


@pytest.mark.asyncio
async def test_recreate_sandbox_reports_failure_without_ids() -> None:
    config = {"configurable": {"thread_id": "thread-1"}}

    with (
        patch("agent.tools.recreate_sandbox.get_config", return_value=config),
        patch(
            "agent.tools.recreate_sandbox.resolve_default_repo",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch(
            "agent.tools.recreate_sandbox.recreate_sandbox_for_thread",
            new_callable=AsyncMock,
            side_effect=RuntimeError("creation failed"),
        ),
    ):
        result = await recreate_sandbox()

    assert result == {"success": False, "error": "creation failed"}


def test_recreate_sandbox_exported() -> None:
    from agent.tools import recreate_sandbox as exported

    assert exported is recreate_sandbox
