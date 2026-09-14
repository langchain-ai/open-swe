from unittest.mock import AsyncMock

from langchain_core.messages import HumanMessage, ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

from agent.middleware.tool_call_timing import (
    TOOL_CALL_ELAPSED_MS_KEY,
    ToolCallTimingMiddleware,
)


def _request() -> ToolCallRequest:
    return AsyncMock(tool_call={"id": "call-1", "name": "execute", "args": {}})


async def test_attaches_elapsed_time_to_tool_message_metadata() -> None:
    clock = iter([1_000_000_000, 2_234_999_999])
    middleware = ToolCallTimingMiddleware(clock=lambda: next(clock))
    original = ToolMessage(
        content="done",
        tool_call_id="call-1",
        response_metadata={"existing": True},
    )

    result = await middleware.awrap_tool_call(_request(), AsyncMock(return_value=original))

    assert isinstance(result, ToolMessage)
    assert result.response_metadata == {
        "existing": True,
        TOOL_CALL_ELAPSED_MS_KEY: 1234,
    }
    assert original.response_metadata == {"existing": True}


async def test_attaches_elapsed_time_inside_command_without_losing_fields() -> None:
    clock = iter([10_000_000, 1_010_000_000])
    middleware = ToolCallTimingMiddleware(clock=lambda: next(clock))
    tool_message = ToolMessage(content="done", tool_call_id="call-1")
    other_message = HumanMessage(content="keep me")
    original = Command(
        graph=Command.PARENT,
        update={"messages": [tool_message, other_message], "value": 42},
        resume={"approved": True},
        goto="next",
    )

    result = await middleware.awrap_tool_call(_request(), AsyncMock(return_value=original))

    assert isinstance(result, Command)
    assert result.graph == Command.PARENT
    assert result.resume == {"approved": True}
    assert result.goto == "next"
    assert result.update["value"] == 42
    timed_message, preserved_message = result.update["messages"]
    assert timed_message.response_metadata[TOOL_CALL_ELAPSED_MS_KEY] == 1000
    assert preserved_message is other_message
    assert tool_message.response_metadata == {}
