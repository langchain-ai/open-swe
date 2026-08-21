import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from langsmith.sandbox import SandboxClientError
from support.sandbox_fakes import FakeSandboxBackend

from agent.middleware.tool_error_handler import ToolErrorMiddleware
from agent.sandboxes.registry import SANDBOX_BACKENDS, set_sandbox_backend
from agent.utils.source_channel import (
    post_sandbox_unreachable_notification,
    sandbox_unreachable_message,
)


def _tool_request(thread_id: str = "thread-1") -> ToolCallRequest:
    runtime = MagicMock(config={"configurable": {"thread_id": thread_id}})
    return ToolCallRequest(
        tool_call={"name": "ls", "args": {"path": "/"}, "id": "tc1"},
        tool=MagicMock(),
        state={},
        runtime=runtime,
    )


@pytest.mark.asyncio
async def test_sandbox_client_error_notifies_and_never_recreates() -> None:
    """A dead sandbox surfaces an error to the user; it is never swapped out.

    Replacing it mid-run gives the agent an empty filesystem while it still
    believes its working tree is intact, silently destroying uncommitted work.
    """
    middleware = ToolErrorMiddleware()
    request = _tool_request()
    set_sandbox_backend("thread-1", FakeSandboxBackend("sb-old"))

    async def handler(_request: ToolCallRequest) -> ToolMessage:
        raise SandboxClientError("Sandbox request timed out: sb-dead")

    with (
        patch(
            "agent.middleware.tool_error_handler.post_sandbox_unreachable_notification",
            new_callable=AsyncMock,
        ) as mock_notify,
        patch(
            "agent.runtime.sandbox._create_sandbox_with_proxy", new_callable=AsyncMock
        ) as mock_create,
    ):
        result = await middleware.awrap_tool_call(request, handler)

    mock_create.assert_not_awaited()
    mock_notify.assert_awaited_once()
    # The dead backend must not linger for the next tool call.
    assert "thread-1" not in SANDBOX_BACKENDS

    assert isinstance(result, ToolMessage)
    assert isinstance(result.content, str)
    payload = json.loads(result.content)
    assert payload["status"] == "error"
    assert payload["error_type"] == "SandboxClientError"
    assert payload["recovery"] == "sandbox_unreachable"
    assert payload["previous_error"] == "Sandbox request timed out: sb-dead"
    assert "will not be replaced" in payload["error"]


def test_unreachable_message_names_the_sandbox_without_claiming_permanence() -> None:
    """The user gets told which sandbox went quiet, not just that one did."""
    message = sandbox_unreachable_message(sandbox_id="sb-dead")

    assert "id sb-dead" in message
    # We only observed silence, so the copy must not assert permanence.
    assert "can't tell whether it will come back" in message


@pytest.mark.asyncio
async def test_unreachable_notification_goes_to_slack_only() -> None:
    """Slack wins over Linear and GitHub, so the user is told exactly once."""
    config = {
        "configurable": {
            "slack_thread": {"channel_id": "C123", "thread_ts": "171.123"},
            "linear_issue": {"id": "lin-1"},
            "repo": {"owner": "langchain-ai", "name": "open-swe"},
            "pr_number": 7,
        }
    }

    with (
        patch(
            "agent.utils.source_channel.post_slack_thread_reply",
            new_callable=AsyncMock,
        ) as mock_slack,
        patch(
            "agent.utils.source_channel.comment_on_linear_issue",
            new_callable=AsyncMock,
        ) as mock_linear,
        patch(
            "agent.utils.source_channel.post_github_comment",
            new_callable=AsyncMock,
        ) as mock_github,
    ):
        await post_sandbox_unreachable_notification(config, sandbox_id="sb-dead")

    mock_slack.assert_awaited_once_with(
        "C123", "171.123", sandbox_unreachable_message(sandbox_id="sb-dead")
    )
    mock_linear.assert_not_called()
    mock_github.assert_not_called()
