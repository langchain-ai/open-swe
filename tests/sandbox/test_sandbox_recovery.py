import json
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from deepagents.backends.protocol import ExecuteResponse, SandboxBackendProtocol
from langchain.agents.middleware import AgentState
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from langsmith.sandbox import SandboxClientError

from agent.middleware.sandbox_circuit_breaker import (
    SandboxCircuitBreakerMiddleware,
    sandbox_unreachable_message,
)
from agent.middleware.tool_error_handler import ToolErrorMiddleware
from agent.utils.sandbox_state import SANDBOX_BACKENDS, clear_sandbox_backend, set_sandbox_backend


class FakeSandboxBackend(SandboxBackendProtocol):
    def __init__(self, sandbox_id: str = "sb-new") -> None:
        self._sandbox_id = sandbox_id

    @property
    def id(self) -> str:
        return self._sandbox_id

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        return ExecuteResponse(output=f"{self.id}: {command}: {timeout}", exit_code=0)


def _tool_request(thread_id: str = "thread-1") -> ToolCallRequest:
    runtime = MagicMock(config={"configurable": {"thread_id": thread_id}})
    return ToolCallRequest(
        tool_call={"name": "ls", "args": {"path": "/"}, "id": "tc1"},
        tool=MagicMock(),
        state={},
        runtime=runtime,
    )


def _sandbox_error_message(tool_call_id: str, sandbox_id: str = "sb-dead") -> ToolMessage:
    return ToolMessage(
        content=json.dumps(
            {
                "error": f"Sandbox request timed out: {sandbox_id}",
                "error_type": "SandboxClientError",
                "status": "error",
            }
        ),
        tool_call_id=tool_call_id,
        status="error",
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

    try:
        with (
            patch(
                "agent.middleware.tool_error_handler.post_sandbox_unreachable_notification",
                new_callable=AsyncMock,
            ) as mock_notify,
            patch("agent.server._create_sandbox_with_proxy", new_callable=AsyncMock) as mock_create,
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
    finally:
        clear_sandbox_backend("thread-1")


def test_repeated_sandbox_errors_trigger_circuit_breaker_once() -> None:
    middleware = SandboxCircuitBreakerMiddleware(threshold=2)
    messages = [
        HumanMessage(content="please fix this"),
        AIMessage(content="", tool_calls=[{"name": "ls", "args": {}, "id": "tc1"}]),
        _sandbox_error_message("tc1"),
        AIMessage(content="", tool_calls=[{"name": "grep", "args": {}, "id": "tc2"}]),
        _sandbox_error_message("tc2"),
        AIMessage(content="", tool_calls=[{"name": "execute", "args": {}, "id": "tc3"}]),
        _sandbox_error_message("tc3"),
    ]

    result = middleware.before_model({"messages": messages}, MagicMock())

    assert result is not None
    assert result["jump_to"] == "end"
    assert len(result["messages"]) == 1
    assert "Sandbox circuit breaker triggered" in result["messages"][0].content

    repeated = middleware.before_model(
        {"messages": [*messages, *result["messages"]]},
        MagicMock(),
    )
    assert repeated is None


def test_circuit_breaker_message_names_the_sandbox() -> None:
    """The user gets told which sandbox went quiet, not just that one did."""
    middleware = SandboxCircuitBreakerMiddleware(threshold=2)
    messages = [
        HumanMessage(content="please fix this"),
        AIMessage(content="", tool_calls=[{"name": "ls", "args": {}, "id": "tc1"}]),
        _sandbox_error_message("tc1"),
        AIMessage(content="", tool_calls=[{"name": "grep", "args": {}, "id": "tc2"}]),
        _sandbox_error_message("tc2"),
        AIMessage(content="", tool_calls=[{"name": "execute", "args": {}, "id": "tc3"}]),
        _sandbox_error_message("tc3"),
    ]

    result = middleware.before_model({"messages": messages}, MagicMock())

    assert result is not None
    content = result["messages"][0].content
    assert "id sb-dead" in content
    # We only observed silence, so the copy must not assert permanence.
    assert "can't tell whether it will come back" in content


@pytest.mark.asyncio
async def test_circuit_breaker_posts_one_user_notification() -> None:
    middleware = SandboxCircuitBreakerMiddleware(threshold=2)
    state = {
        "messages": [
            AIMessage(
                content=(
                    "Sandbox circuit breaker triggered: 3 consecutive sandbox tool failures "
                    "against sb-dead."
                )
            )
        ]
    }
    config = {
        "configurable": {
            "slack_thread": {"channel_id": "C123", "thread_ts": "171.123"},
            "linear_issue": {"id": "lin-1"},
            "repo": {"owner": "langchain-ai", "name": "open-swe"},
            "pr_number": 7,
        }
    }

    with (
        patch("agent.middleware.sandbox_circuit_breaker.get_config", return_value=config),
        patch(
            "agent.middleware.sandbox_circuit_breaker.post_slack_thread_reply",
            new_callable=AsyncMock,
        ) as mock_slack,
        patch(
            "agent.middleware.sandbox_circuit_breaker.comment_on_linear_issue",
            new_callable=AsyncMock,
        ) as mock_linear,
        patch(
            "agent.middleware.sandbox_circuit_breaker.post_github_comment",
            new_callable=AsyncMock,
        ) as mock_github,
    ):
        result = await middleware.aafter_agent(cast(AgentState[Any], state), MagicMock())

    assert result is None
    mock_slack.assert_awaited_once_with(
        "C123", "171.123", sandbox_unreachable_message(sandbox_id="sb-dead")
    )
    mock_linear.assert_not_called()
    mock_github.assert_not_called()
