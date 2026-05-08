import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from langsmith.sandbox import SandboxClientError

from agent.middleware.sandbox_circuit_breaker import (
    SANDBOX_UNRECOVERABLE_MESSAGE,
    SandboxCircuitBreakerMiddleware,
)
from agent.middleware.tool_error_handler import ToolErrorMiddleware
from agent.utils.sandbox_state import SANDBOX_BACKENDS


class FakeSandboxBackend:
    id = "sb-new"

    def execute(self, _command: str) -> None:
        return None


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
async def test_sandbox_client_error_recreates_sandbox() -> None:
    middleware = ToolErrorMiddleware()
    request = _tool_request()
    backend = FakeSandboxBackend()

    async def handler(_request: ToolCallRequest) -> ToolMessage:
        raise SandboxClientError("Sandbox request timed out: sb-dead")

    try:
        with (
            patch("agent.server._recreate_sandbox", new_callable=AsyncMock) as mock_recreate,
            patch("agent.server.client") as mock_client,
        ):
            mock_recreate.return_value = backend
            mock_client.threads.update = AsyncMock()

            result = await middleware.awrap_tool_call(request, handler)

        assert isinstance(result, ToolMessage)
        mock_recreate.assert_awaited_once_with("thread-1")
        mock_client.threads.update.assert_awaited_once_with(
            thread_id="thread-1",
            metadata={"sandbox_id": "sb-new"},
        )
        assert SANDBOX_BACKENDS["thread-1"] is backend

        payload = json.loads(result.content)
        assert payload["status"] == "error"
        assert "sb-new" in payload["error"]
        assert "SandboxClientError" not in payload["error"]
    finally:
        SANDBOX_BACKENDS.pop("thread-1", None)


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
        result = await middleware.aafter_agent(state, MagicMock())

    assert result is None
    mock_slack.assert_awaited_once_with("C123", "171.123", SANDBOX_UNRECOVERABLE_MESSAGE)
    mock_linear.assert_not_called()
    mock_github.assert_not_called()
