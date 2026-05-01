import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import ToolMessage

from agent.middleware.open_pr import open_pr_if_needed


def _tool_message(payload: dict[str, object]) -> ToolMessage:
    return ToolMessage(
        content=json.dumps(payload),
        tool_call_id="commit-and-open-pr",
        name="commit_and_open_pr",
    )


@pytest.mark.asyncio
async def test_skips_permanent_push_failure() -> None:
    payload = {
        "success": False,
        "error": (
            "PERMANENT_FAILURE: do not retry. Git push was rejected with a 403 "
            "permission denied error."
        ),
        "pr_url": None,
    }

    with (
        patch(
            "agent.middleware.open_pr.get_config",
            return_value={
                "configurable": {
                    "thread_id": "thread-permanent",
                    "repo": {"owner": "org", "name": "repo"},
                }
            },
        ),
        patch(
            "agent.middleware.open_pr.get_sandbox_backend", new_callable=AsyncMock
        ) as get_sandbox,
    ):
        await open_pr_if_needed.aafter_agent(
            {"messages": [_tool_message(payload)]},
            MagicMock(),
        )

    get_sandbox.assert_not_called()


@pytest.mark.asyncio
async def test_safety_net_runs_for_non_permanent_failure() -> None:
    payload = {
        "success": False,
        "error": "Git push failed: Updates were rejected because the remote contains work",
        "pr_url": None,
    }

    with (
        patch(
            "agent.middleware.open_pr.get_config",
            return_value={
                "configurable": {
                    "thread_id": "thread-recoverable",
                    "repo": {"owner": "org", "name": "repo"},
                }
            },
        ),
        patch(
            "agent.middleware.open_pr.get_sandbox_backend",
            new_callable=AsyncMock,
            return_value=None,
        ) as get_sandbox,
    ):
        await open_pr_if_needed.aafter_agent(
            {"messages": [_tool_message(payload)]},
            MagicMock(),
        )

    get_sandbox.assert_awaited_once_with("thread-recoverable")
