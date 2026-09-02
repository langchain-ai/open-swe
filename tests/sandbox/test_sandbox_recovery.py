import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from deepagents.backends.protocol import ExecuteResponse, SandboxBackendProtocol
from langchain_core.messages import ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from langsmith.sandbox import (
    SandboxConnectionError,
    SandboxOperationError,
    SandboxRetryableConnectionError,
)

from agent.middleware.sandbox_circuit_breaker import (
    post_sandbox_unreachable_notification,
    sandbox_unreachable_message,
)
from agent.middleware.tool_error_handler import ToolErrorMiddleware
from agent.sandboxes.state import (
    SANDBOX_BACKENDS,
    set_sandbox_backend,
)


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


@pytest.mark.asyncio
async def test_unreachable_sandbox_notifies_then_ends_the_run() -> None:
    """A dead sandbox tells the user once, then kills the run; it is never swapped out.

    Replacing it mid-run gives the agent an empty filesystem while it still
    believes its working tree is intact, silently destroying uncommitted work.
    Handing the model an error message instead would leave every later sandbox
    call hitting the same dead backend and notifying the user again.
    """
    middleware = ToolErrorMiddleware()
    request = _tool_request()
    set_sandbox_backend("thread-1", FakeSandboxBackend("sb-old"))

    async def handler(_request: ToolCallRequest) -> ToolMessage:
        raise SandboxConnectionError("WebSocket upgrade to sb-dead failed (no valid HTTP response)")

    try:
        with (
            patch(
                "agent.middleware.tool_error_handler.post_sandbox_unreachable_notification",
                new_callable=AsyncMock,
            ) as mock_notify,
            patch("agent.server._create_sandbox_with_proxy", new_callable=AsyncMock) as mock_create,
            pytest.raises(SandboxConnectionError),
        ):
            await middleware.awrap_tool_call(request, handler)

        mock_create.assert_not_awaited()
        mock_notify.assert_awaited_once()
    finally:
        SANDBOX_BACKENDS.pop("thread-1", None)


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
        await post_sandbox_unreachable_notification(config, sandbox_id="sb-dead")

    mock_slack.assert_awaited_once_with(
        "C123", "171.123", sandbox_unreachable_message(sandbox_id="sb-dead")
    )
    mock_linear.assert_not_called()
    mock_github.assert_not_called()


@pytest.mark.asyncio
async def test_transient_sandbox_error_keeps_the_sandbox_and_asks_for_a_retry() -> None:
    """A rejected WebSocket upgrade is a gateway blip, not a dead sandbox.

    The command never started, so the backend stays bound and the user is not
    told their sandbox went quiet — the model is told to retry the tool call.
    """
    middleware = ToolErrorMiddleware()
    request = _tool_request("thread-transient")
    set_sandbox_backend("thread-transient", FakeSandboxBackend("sb-live"))

    async def handler(_request: ToolCallRequest) -> ToolMessage:
        raise SandboxRetryableConnectionError(
            "WebSocket upgrade temporarily rejected by server (HTTP 503): "
            "server rejected WebSocket connection: HTTP 503"
        )

    try:
        with patch(
            "agent.middleware.tool_error_handler.post_sandbox_unreachable_notification",
            new_callable=AsyncMock,
        ) as mock_notify:
            result = await middleware.awrap_tool_call(request, handler)

        mock_notify.assert_not_awaited()
        assert "thread-transient" in SANDBOX_BACKENDS

        assert isinstance(result, ToolMessage)
        assert isinstance(result.content, str)
        payload = json.loads(result.content)
        assert payload["recovery"] == "sandbox_transient"
        assert payload["error_type"] == "SandboxRetryableConnectionError"
        assert "nothing ran and nothing changed" in payload["error"]
        assert "will not be replaced" not in payload["error"]
    finally:
        SANDBOX_BACKENDS.pop("thread-transient", None)


@pytest.mark.asyncio
async def test_command_level_sandbox_error_does_not_end_the_run() -> None:
    """A failed command says nothing about whether the sandbox is reachable.

    ``SandboxOperationError`` covers command error frames — a missing binary, an
    expired session — so killing the run and telling the user their sandbox died
    would be both wrong and unrecoverable.
    """
    middleware = ToolErrorMiddleware()
    request = _tool_request("thread-op")

    async def handler(_request: ToolCallRequest) -> ToolMessage:
        raise SandboxOperationError("CommandNotFound: no such file or directory: fzf")

    with patch(
        "agent.middleware.tool_error_handler.post_sandbox_unreachable_notification",
        new_callable=AsyncMock,
    ) as mock_notify:
        result = await middleware.awrap_tool_call(request, handler)

    mock_notify.assert_not_awaited()
    assert isinstance(result, ToolMessage)
    assert isinstance(result.content, str)
    payload = json.loads(result.content)
    assert payload["status"] == "error"
    assert payload["error_type"] == "SandboxOperationError"
    assert "recovery" not in payload
