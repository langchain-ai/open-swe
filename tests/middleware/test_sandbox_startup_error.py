from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage

from agent.middleware.sandbox_startup_error import (
    STARTUP_ERROR_MESSAGE,
    SandboxStartupErrorMiddleware,
)
from agent.utils.sandbox_state import (
    SandboxBackendNotReady,
    SandboxBackendProxy,
    clear_sandbox_backend,
)


@pytest.mark.asyncio
async def test_sync_backend_access_raises_typed_exception() -> None:
    proxy = SandboxBackendProxy(thread_id="thread-1")
    try:
        with pytest.raises(SandboxBackendNotReady) as excinfo:
            proxy.execute("echo ok")
    finally:
        clear_sandbox_backend("thread-1")

    assert excinfo.value.thread_id == "thread-1"


@pytest.mark.asyncio
async def test_startup_error_middleware_posts_slack_reply() -> None:
    middleware = SandboxStartupErrorMiddleware()
    proxy = SandboxBackendProxy(thread_id="thread-1")

    async def handler(_request):
        proxy.execute("echo ok")
        raise AssertionError("unreachable")

    config = {
        "configurable": {
            "slack_thread": {"channel_id": "C123", "thread_ts": "171.123"},
        }
    }

    try:
        with (
            patch(
                "agent.middleware.sandbox_startup_error.get_config",
                return_value=config,
            ),
            patch(
                "agent.middleware.sandbox_startup_error.post_slack_thread_reply",
                new_callable=AsyncMock,
            ) as mock_slack,
        ):
            result = await middleware.awrap_model_call(MagicMock(), handler)
    finally:
        clear_sandbox_backend("thread-1")

    assert result is not None
    assert result["jump_to"] == "end"
    assert len(result["messages"]) == 1
    assert isinstance(result["messages"][0], AIMessage)
    assert "Sandbox startup error" in result["messages"][0].content
    mock_slack.assert_awaited_once_with("C123", "171.123", STARTUP_ERROR_MESSAGE)


@pytest.mark.asyncio
async def test_startup_error_middleware_reraises_unrelated_errors() -> None:
    middleware = SandboxStartupErrorMiddleware()

    async def handler(_request):
        raise ValueError("something else")

    with pytest.raises(ValueError, match="something else"):
        await middleware.awrap_model_call(MagicMock(), handler)


@pytest.mark.asyncio
async def test_startup_error_middleware_falls_back_to_linear() -> None:
    middleware = SandboxStartupErrorMiddleware()

    async def handler(_request):
        raise SandboxBackendNotReady(thread_id="thread-1", reason="sync tool call")

    config = {
        "configurable": {
            "linear_issue": {"id": "lin-1"},
        }
    }

    with (
        patch(
            "agent.middleware.sandbox_startup_error.get_config",
            return_value=config,
        ),
        patch(
            "agent.middleware.sandbox_startup_error.post_slack_thread_reply",
            new_callable=AsyncMock,
        ) as mock_slack,
        patch(
            "agent.middleware.sandbox_startup_error.comment_on_linear_issue",
            new_callable=AsyncMock,
        ) as mock_linear,
    ):
        result = await middleware.awrap_model_call(MagicMock(), handler)

    assert result is not None
    assert result["jump_to"] == "end"
    mock_slack.assert_not_called()
    mock_linear.assert_awaited_once_with("lin-1", STARTUP_ERROR_MESSAGE)
