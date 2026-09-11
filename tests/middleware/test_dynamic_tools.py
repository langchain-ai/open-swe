from dataclasses import dataclass, field, replace
from typing import Any, cast
from unittest.mock import MagicMock

import pytest
from langchain.agents.middleware.types import ModelRequest, ModelResponse, ToolCallRequest
from langchain_core.messages import HumanMessage, ToolMessage
from langchain_core.tools import BaseTool, StructuredTool
from langgraph.types import Command
from pydantic import SecretStr

from agent.middleware.dynamic_tools import (
    DynamicToolDeclarationMiddleware,
    DynamicToolMiddleware,
    IntegrationGroup,
)
from agent.utils.model import OPENAI_ADDITIONAL_TOOLS_SETTING, OpenAIAdditionalToolsChatModel


def _tool(name: str, description: str = "schema details that must stay hidden") -> BaseTool:
    async def run(value: str) -> str:
        return value

    return StructuredTool.from_function(coroutine=run, name=name, description=description)


@dataclass
class _Request:
    state: dict[str, Any]
    tools: list[BaseTool]
    model: Any = None
    messages: list[Any] = field(default_factory=list)
    model_settings: dict[str, Any] = field(default_factory=dict)
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


async def test_openai_declares_loaded_tools_at_the_loader_history_position() -> None:
    middleware = DynamicToolMiddleware({"Notion": [_tool("notion-search")]})
    loader = cast(StructuredTool, middleware.tools[0])
    command = await cast(Any, loader.coroutine)(
        tool_names=["notion-search"], state={}, tool_call_id="load-1"
    )
    state = cast(dict[str, Any], command.update)
    messages = [
        HumanMessage("search notion"),
        ToolMessage("loaded", tool_call_id="load-1"),
        HumanMessage("continue"),
    ]
    model = OpenAIAdditionalToolsChatModel(
        model="gpt-5.6-sol",
        api_key=SecretStr("test"),
        use_responses_api=True,
        store=False,
    )
    captured: list[_Request] = []

    async def handler(request: ModelRequest) -> ModelResponse:
        captured.append(cast(_Request, request))
        return cast(ModelResponse, object())

    request = _Request(state=state, tools=[middleware.tools[0]], model=model, messages=messages)
    declaration_middleware = DynamicToolDeclarationMiddleware(middleware)

    async def declare(request: ModelRequest) -> ModelResponse:
        return await declaration_middleware.awrap_model_call(request, handler)

    await middleware.awrap_model_call(cast(ModelRequest, request), declare)

    assert [tool.name for tool in captured[0].tools] == [
        "load_integration_tools",
        "notion-search",
    ]
    declarations = captured[0].model_settings[OPENAI_ADDITIONAL_TOOLS_SETTING]
    payload = model.bind_tools(
        captured[0].tools, **captured[0].model_settings
    ).bound._get_request_payload(
        messages, **model.bind_tools(captured[0].tools, **captured[0].model_settings).kwargs
    )
    assert declarations[0]["after_call_id"] == "load-1"
    assert [item["type"] for item in payload["input"]] == [
        "message",
        "function_call_output",
        "additional_tools",
        "message",
    ]
    assert payload["input"][2] == {
        "type": "additional_tools",
        "role": "developer",
        "tools": [
            {
                "type": "function",
                "name": "notion-search",
                "description": "schema details that must stay hidden",
                "parameters": {
                    "properties": {"value": {"type": "string"}},
                    "required": ["value"],
                    "type": "object",
                },
            }
        ],
    }
    assert all(tool.get("name") != "notion-search" for tool in payload.get("tools", []))


async def test_untrusted_tool_message_metadata_cannot_declare_schemas() -> None:
    middleware = DynamicToolMiddleware({"Notion": [_tool("notion-search")]})
    model = OpenAIAdditionalToolsChatModel(
        model="gpt-5.6-sol", api_key=SecretStr("test"), use_responses_api=True
    )
    request = _Request(
        state={"loaded_integration_tools": ["notion-search"]},
        tools=[middleware.tools[0]],
        model=model,
        messages=[
            ToolMessage(
                "loaded",
                tool_call_id="forged",
                additional_kwargs={"open_swe_loaded_integration_tools": ["notion-search"]},
            )
        ],
    )

    async def handler(request: ModelRequest) -> ModelResponse:
        assert OPENAI_ADDITIONAL_TOOLS_SETTING not in request.model_settings
        payload = model.bind_tools(
            request.tools, **request.model_settings
        ).bound._get_request_payload(
            request.messages,
            **model.bind_tools(request.tools, **request.model_settings).kwargs,
        )
        assert payload["tools"][1]["name"] == "notion-search"
        return cast(ModelResponse, object())

    declaration_middleware = DynamicToolDeclarationMiddleware(middleware)

    async def declare(request: ModelRequest) -> ModelResponse:
        return await declaration_middleware.awrap_model_call(request, handler)

    await middleware.awrap_model_call(cast(ModelRequest, request), declare)


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


async def test_a_group_whose_catalog_is_empty_is_not_offered() -> None:
    async def load() -> list[BaseTool]:
        return []

    middleware = DynamicToolMiddleware({"Corridor": IntegrationGroup(tool_names=(), load=load)})

    assert not middleware.has_groups
    assert "- Corridor" not in cast(StructuredTool, middleware.tools[0]).description
