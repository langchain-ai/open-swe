import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from deepagents.backends.protocol import ExecuteResponse, SandboxBackendProtocol
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from langsmith.sandbox import SandboxClientError

from agent.middleware.sandbox_circuit_breaker import (
    SANDBOX_UNRECOVERABLE_MESSAGE,
    SandboxCircuitBreakerMiddleware,
)
from agent.middleware.tool_error_handler import ToolErrorMiddleware
from agent.utils.sandbox_state import SANDBOX_BACKENDS, clear_sandbox_backend, set_sandbox_backend


class FakeSandboxBackend(SandboxBackendProtocol):
    id = "sb-new"

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
async def test_sandbox_client_error_recreates_sandbox() -> None:
    middleware = ToolErrorMiddleware()
    request = _tool_request()
    old_backend = FakeSandboxBackend()
    backend = FakeSandboxBackend()
    old_backend.id = "sb-old"
    backend.id = "sb-new"
    proxy = set_sandbox_backend("thread-1", old_backend)

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
        assert SANDBOX_BACKENDS["thread-1"] is proxy
        current_backend = proxy.current
        from agent.utils.sandbox_safety import AuditingSandboxWrapper

        if isinstance(current_backend, AuditingSandboxWrapper):
            current_backend = current_backend._raw_sandbox
        assert current_backend is backend
        assert proxy.id == "sb-new"
        assert proxy.execute("echo ok").output == "sb-new: echo ok: None"

        payload = json.loads(result.content)
        assert payload["status"] == "error"
        assert payload["error_type"] == "SandboxClientError"
        assert payload["recovery"] == "sandbox_recreated_after_client_error"
        assert payload["previous_error"] == "Sandbox request timed out: sb-dead"
        assert "sb-new" in payload["error"]
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


def test_repeated_sandbox_recreations_trigger_circuit_breaker() -> None:
    middleware = SandboxCircuitBreakerMiddleware(threshold=2)
    messages = [
        HumanMessage(content="please fix this"),
        AIMessage(content="", tool_calls=[{"name": "ls", "args": {}, "id": "tc1"}]),
        ToolMessage(
            content=json.dumps(
                {
                    "error_type": "SandboxClientError",
                    "previous_error": "Sandbox request timed out: sb-old-1",
                    "recovery": "sandbox_recreated_after_client_error",
                    "sandbox_id": "sb-new-1",
                    "status": "error",
                }
            ),
            tool_call_id="tc1",
            status="error",
        ),
        AIMessage(content="", tool_calls=[{"name": "grep", "args": {}, "id": "tc2"}]),
        ToolMessage(
            content=json.dumps(
                {
                    "error_type": "SandboxClientError",
                    "previous_error": "Sandbox request timed out: sb-new-1",
                    "recovery": "sandbox_recreated_after_client_error",
                    "sandbox_id": "sb-new-2",
                    "status": "error",
                }
            ),
            tool_call_id="tc2",
            status="error",
        ),
        AIMessage(content="", tool_calls=[{"name": "execute", "args": {}, "id": "tc3"}]),
        ToolMessage(
            content=json.dumps(
                {
                    "error_type": "SandboxClientError",
                    "previous_error": "Sandbox request timed out: sb-new-2",
                    "recovery": "sandbox_recreated_after_client_error",
                    "sandbox_id": "sb-new-3",
                    "status": "error",
                }
            ),
            tool_call_id="tc3",
            status="error",
        ),
    ]

    result = middleware.before_model({"messages": messages}, MagicMock())

    assert result is not None
    assert result["jump_to"] == "end"
    assert "consecutive sandbox recreations" in result["messages"][0].content


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
