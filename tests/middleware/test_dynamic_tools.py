from dataclasses import dataclass, replace
from typing import Any, cast
from unittest.mock import MagicMock

import pytest
from langchain.agents.middleware.types import ModelRequest, ModelResponse, ToolCallRequest
from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool, StructuredTool
from langgraph.types import Command

from agent.middleware.dynamic_tools import DynamicToolMiddleware, IntegrationGroup


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

    assert "- notion-search (integration: Notion)" in loader.description
    assert "- notion-update-page (integration: Notion)" in loader.description
    assert 'Example: {"tool_names":["notion-search"]}' in loader.description
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
    subagent = _general_purpose_subagent(MagicMock(), tools=[], dynamic_tools=middleware)

    assert middleware in subagent.get("middleware", [])


async def test_a_lazy_group_is_not_built_until_it_is_loaded() -> None:
    builds = 0

    async def load() -> list[BaseTool]:
        nonlocal builds
        builds += 1
        return [_tool("analyzePlan")]

    middleware = DynamicToolMiddleware(
        {"Corridor": IntegrationGroup(tool_names=("analyzePlan",), load=load)}
    )
    loader = cast(StructuredTool, middleware.tools[0])

    # The catalog reaches the model without the group ever being built.
    assert "- analyzePlan (integration: Corridor)" in loader.description
    assert 'Example: {"tool_names":["analyzePlan"]}' in loader.description
    assert builds == 0

    coroutine = cast(Any, loader.coroutine)
    command = await coroutine(tool_names=["analyzePlan"], state={}, tool_call_id="load-1")
    assert isinstance(command, Command)
    assert builds == 1

    loaded_state = cast(dict[str, Any], command.update)
    routed: list[str] = []

    async def tool_handler(request: ToolCallRequest) -> ToolMessage:
        assert request.tool is not None
        routed.append(request.tool.name)
        return ToolMessage(content="ok", tool_call_id=request.tool_call["id"])

    call = _Request(
        state=loaded_state,
        tools=[],
        tool_call={"name": "analyzePlan", "args": {"value": "x"}, "id": "call-1"},
    )
    await middleware.awrap_tool_call(cast(ToolCallRequest, call), tool_handler)
    assert routed == ["analyzePlan"]
    # Built once and reused, not re-fetched per call.
    assert builds == 1


@pytest.mark.parametrize("qualified_name", ["Corridor:analyzePlan", "Corridor: analyzePlan"])
async def test_catalog_qualified_names_are_normalized(qualified_name: str) -> None:
    builds = 0
    calls = 0

    async def analyze_plan(value: str) -> str:
        nonlocal calls
        calls += 1
        return value

    async def load() -> list[BaseTool]:
        nonlocal builds
        builds += 1
        return [
            StructuredTool.from_function(
                coroutine=analyze_plan,
                name="analyzePlan",
                description="Analyze an implementation plan.",
            )
        ]

    middleware = DynamicToolMiddleware(
        {"Corridor": IntegrationGroup(tool_names=("analyzePlan",), load=load)}
    )
    coroutine = cast(Any, cast(StructuredTool, middleware.tools[0]).coroutine)

    command = await coroutine(tool_names=[qualified_name], state={}, tool_call_id="load-1")
    assert isinstance(command, Command)
    assert builds == 1
    loaded_state = cast(dict[str, Any], command.update)
    assert loaded_state["loaded_integration_tools"] == ["analyzePlan"]

    async def tool_handler(request: ToolCallRequest) -> ToolMessage:
        assert request.tool is not None
        result = await request.tool.ainvoke(request.tool_call["args"])
        return ToolMessage(content=result, tool_call_id=request.tool_call["id"])

    call = _Request(
        state=loaded_state,
        tools=[],
        tool_call={"name": "analyzePlan", "args": {"value": "plan"}, "id": "call-1"},
    )
    result = await middleware.awrap_tool_call(cast(ToolCallRequest, call), tool_handler)

    assert isinstance(result, ToolMessage)
    assert result.content == "plan"
    assert builds == 1
    assert calls == 1


async def test_unknown_qualified_name_is_rejected() -> None:
    middleware = DynamicToolMiddleware({"Corridor": [_tool("analyzePlan")]})
    coroutine = cast(Any, cast(StructuredTool, middleware.tools[0]).coroutine)

    command = await coroutine(tool_names=["Other:analyzePlan"], state={}, tool_call_id="load-1")

    assert isinstance(command, Command)
    message = cast(dict[str, Any], command.update)["messages"][0]
    assert message.status == "error"
    assert message.content == "Unknown integration tools: Other:analyzePlan"


async def test_a_group_that_fails_to_build_is_reported_not_raised() -> None:
    async def load() -> list[BaseTool]:
        raise RuntimeError("mcp unreachable")

    middleware = DynamicToolMiddleware(
        {"Corridor": IntegrationGroup(tool_names=("analyzePlan",), load=load)}
    )
    coroutine = cast(Any, cast(StructuredTool, middleware.tools[0]).coroutine)

    command = await coroutine(tool_names=["analyzePlan"], state={}, tool_call_id="load-1")
    assert isinstance(command, Command)
    message = cast(dict[str, Any], command.update)["messages"][0]
    assert message.status == "error"
    assert "unavailable right now" in message.content


async def test_a_group_retries_after_a_transient_build_failure() -> None:
    attempts = 0
    tool = _tool("analyzePlan")

    async def load() -> list[BaseTool]:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("mcp temporarily unreachable")
        return [tool]

    middleware = DynamicToolMiddleware(
        {"Corridor": IntegrationGroup(tool_names=("analyzePlan",), load=load)}
    )

    assert await middleware._build(["analyzePlan"]) == ["analyzePlan"]
    assert await middleware._build(["analyzePlan"]) == []
    assert attempts == 2


async def test_a_group_whose_catalog_is_empty_is_not_offered() -> None:
    async def load() -> list[BaseTool]:
        return []

    middleware = DynamicToolMiddleware({"Corridor": IntegrationGroup(tool_names=(), load=load)})

    assert not middleware.has_groups
    assert "- Corridor" not in cast(StructuredTool, middleware.tools[0]).description


def test_the_static_corridor_catalog_matches_what_the_loader_exposes() -> None:
    from agent.tool_loaders.corridor_mcp import _ALLOWED_TOOL_NAMES, CORRIDOR_TOOL_NAMES

    assert set(CORRIDOR_TOOL_NAMES) == set(_ALLOWED_TOOL_NAMES)
