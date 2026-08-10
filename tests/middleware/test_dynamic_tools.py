from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, cast
from unittest.mock import MagicMock

import pytest
from langchain.agents.middleware.types import ModelRequest, ModelResponse, ToolCallRequest
from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool, StructuredTool
from langgraph.types import Command

from agent.middleware.dynamic_tools import DynamicToolMiddleware


def _tool(name: str, description: str = "schema details that must stay hidden") -> BaseTool:
    async def run(value: str) -> str:
        return value

    return StructuredTool.from_function(coroutine=run, name=name, description=description)


@dataclass
class _Request:
    state: dict[str, Any]
    tools: list[BaseTool]
    tool_call: dict[str, Any] | None = None
    tool: BaseTool | None = None

    def override(self, **kwargs: Any) -> _Request:
        return replace(self, **kwargs)


async def test_dynamic_tools_load_only_selected_schemas_and_route_calls() -> None:
    notion_search = _tool("notion-search")
    notion_update = _tool("notion-update-page")
    middleware = DynamicToolMiddleware({"Notion": [notion_search, notion_update]})
    loader = cast(StructuredTool, middleware.tools[0])

    assert "notion-search, notion-update-page" in loader.description
    assert "schema details that must stay hidden" not in loader.description
    schema = cast(Any, loader.tool_call_schema).model_json_schema()
    assert set(schema["properties"]) == {"tool_names"}

    coroutine = cast(Any, loader.coroutine)
    command = await coroutine(tool_names=["notion-search"], state={}, tool_call_id="load-1")
    assert isinstance(command, Command)
    loaded_state = cast(dict[str, Any], command.update)
    assert loaded_state["loaded_integration_tools"] == ["notion-search"]
    assert "next turn" in loaded_state["messages"][0].content

    visible: list[str] = []

    async def model_handler(request: ModelRequest) -> ModelResponse:
        visible.extend(tool.name for tool in request.tools if isinstance(tool, BaseTool))
        return cast(ModelResponse, object())

    model_request = _Request(state=loaded_state, tools=[_tool("static")])
    await middleware.awrap_model_call(cast(ModelRequest, model_request), model_handler)
    assert visible == ["static", "notion-search"]

    routed: list[str] = []

    async def tool_handler(request: ToolCallRequest) -> ToolMessage:
        assert request.tool is not None
        routed.append(request.tool.name)
        return ToolMessage(content="ok", tool_call_id=request.tool_call["id"])

    loaded_call = _Request(
        state=loaded_state,
        tools=[],
        tool_call={"name": "notion-search", "args": {"value": "x"}, "id": "call-1"},
    )
    result = await middleware.awrap_tool_call(cast(ToolCallRequest, loaded_call), tool_handler)
    assert isinstance(result, ToolMessage)
    assert routed == ["notion-search"]

    unloaded_call = replace(
        loaded_call,
        tool_call={"name": "notion-update-page", "args": {}, "id": "call-2"},
    )
    result = await middleware.awrap_tool_call(cast(ToolCallRequest, unloaded_call), tool_handler)
    assert isinstance(result, ToolMessage)
    assert result.status == "error"
    assert routed == ["notion-search"]

    with pytest.raises(ValueError, match="Duplicate integration tool name"):
        DynamicToolMiddleware({"Notion": [_tool("static")]}, reserved_names={"static"})


def test_general_purpose_subagent_includes_dynamic_tools() -> None:
    from agent.server import _general_purpose_subagent

    middleware = DynamicToolMiddleware({"Notion": [_tool("notion-search")]})
    subagent = _general_purpose_subagent(MagicMock(), dynamic_tools=middleware)

    assert middleware in subagent.get("middleware", [])
