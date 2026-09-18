from unittest.mock import AsyncMock

import pytest
from langchain.tools.tool_node import ToolCallRequest
from langchain_core.messages import ToolMessage
from langgraph.runtime import Runtime

from agent.middleware.exclude_tools import ExcludeToolsMiddleware


@pytest.mark.asyncio
async def test_disabled_subagent_call_is_rejected_without_execution() -> None:
    middleware = ExcludeToolsMiddleware(excluded=frozenset({"task"}), blocked=frozenset({"task"}))
    request = ToolCallRequest(
        tool_call={"name": "task", "args": {}, "id": "delegation", "type": "tool_call"},
        tool=None,
        state={"messages": []},
        runtime=Runtime(),
    )
    handler = AsyncMock()
    result = await middleware.awrap_tool_call(request, handler)
    assert isinstance(result, ToolMessage)
    assert result.status == "error"
    assert result.tool_call_id == "delegation"
    handler.assert_not_awaited()
