from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.messages import ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest

from agent.middleware.tool_error_handler import ToolErrorMiddleware


def _tool_request() -> ToolCallRequest:
    return ToolCallRequest(
        tool_call={"name": "save_comment", "args": {"issueId": "ENT-1642"}, "id": "call-1"},
        tool=MagicMock(),
        state={},
        runtime=MagicMock(),
    )


@pytest.mark.asyncio
async def test_short_circuits_after_three_identical_failures() -> None:
    middleware = ToolErrorMiddleware()
    request = _tool_request()
    handler = AsyncMock(side_effect=RuntimeError("invalid arguments"))

    results = [await middleware.awrap_tool_call(request, handler) for _ in range(3)]
    blocked = await middleware.awrap_tool_call(request, handler)

    assert handler.await_count == 3
    assert all(isinstance(result, ToolMessage) for result in results)
    assert isinstance(blocked, ToolMessage)
    assert blocked.status == "error"
    assert "failed repeatedly" in blocked.content
