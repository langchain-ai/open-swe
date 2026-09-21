from collections.abc import Awaitable, Callable, Sequence
from typing import Self, cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain.agents import create_agent
from langchain.agents.middleware import LLMToolSelectorMiddleware
from langchain.agents.middleware.types import ModelRequest, ModelResponse, ToolCallRequest
from langchain_anthropic import ChatAnthropic
from langchain_core.language_models import BaseChatModel
from langchain_core.language_models.fake_chat_models import (
    FakeListChatModel,
    FakeMessagesListChatModel,
)
from langchain_core.messages import AIMessage, HumanMessage, ToolCall, ToolMessage
from langchain_core.tools import BaseTool, StructuredTool
from langchain_openai import ChatOpenAI
from langgraph.prebuilt.tool_node import ToolRuntime

from agent.middleware.dynamic_tools import DynamicToolMiddleware, IntegrationGroup


def _tool(name: str) -> BaseTool:
    async def run(value: str = "") -> str:
        return value

    return StructuredTool.from_function(coroutine=run, name=name, description=f"Use {name}.")


def _request(
    model: BaseChatModel,
    tools: list[BaseTool | dict[str, object]] | None = None,
) -> ModelRequest:
    return ModelRequest(
        model=model,
        messages=[HumanMessage("help")],
        tools=tools or [],
        state={"messages": []},
    )


def _tool_request(name: str, tool: BaseTool | None = None) -> ToolCallRequest:
    call = ToolCall(name=name, args={"value": "result"}, id="call-1")
    return ToolCallRequest(call, tool, {}, cast(ToolRuntime, None))


async def _visible_tools(
    middleware: DynamicToolMiddleware, request: ModelRequest
) -> list[BaseTool | dict[str, object]]:
    visible: list[BaseTool | dict[str, object]] = []

    async def handler(model_request: ModelRequest) -> ModelResponse:
        visible.extend(model_request.tools)
        return ModelResponse(result=[])

    await middleware.awrap_model_call(request, handler)
    return visible


@pytest.mark.parametrize(
    "model,search_type",
    [
        (
            ChatAnthropic(model="claude-sonnet-4-6", api_key="test"),
            "tool_search_tool_bm25_20251119",
        ),
        (ChatAnthropic(model="claude-opus-5", api_key="test"), "tool_search_tool_bm25_20251119"),
        (ChatAnthropic(model="claude-haiku-4-5", api_key="test"), "tool_search_tool_bm25_20251119"),
        (ChatOpenAI(model="gpt-5.5", api_key="test"), "tool_search"),
        (ChatOpenAI(model="gpt-5.6-sol", api_key="test"), "tool_search"),
    ],
)
async def test_supported_direct_models_use_native_deferral(
    model: BaseChatModel, search_type: str
) -> None:
    static = _tool("static")
    middleware = DynamicToolMiddleware({"MCP": [_tool("integration")]})

    visible = await _visible_tools(middleware, _request(model, [static]))

    assert visible[0] is static
    deferred = cast(BaseTool, visible[1])
    assert deferred.name == "integration"
    assert deferred.extras == {"defer_loading": True}
    provider_search = cast(dict[str, object], visible[2])
    assert provider_search["type"] == search_type


@pytest.mark.parametrize(
    "model",
    [
        ChatAnthropic(model="claude-3-7-sonnet-latest", api_key="test"),
        ChatAnthropic(model="claude-haiku-4-4", api_key="test"),
        ChatOpenAI(model="gpt-5.4", api_key="test"),
        ChatOpenAI(
            model="gpt-5.6-sol",
            api_key="test",
            base_url="https://gateway.smith.langchain.com/openai/v1",
        ),
        FakeListChatModel(responses=["unused"]),
    ],
)
async def test_fallback_selects_only_integrations_and_preserves_request_tools(
    model: BaseChatModel, monkeypatch: pytest.MonkeyPatch
) -> None:
    static = _tool("static")
    provider_tool: dict[str, object] = {"type": "web_search"}
    first = _tool("first-integration")
    second = _tool("second-integration")
    seen: list[str] = []

    async def select(
        _self: LLMToolSelectorMiddleware,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        seen.extend(tool.name for tool in request.tools if isinstance(tool, BaseTool))
        return await handler(request.override(tools=[second]))

    monkeypatch.setattr(LLMToolSelectorMiddleware, "awrap_model_call", select)
    middleware = DynamicToolMiddleware({"MCP": [first, second]})

    visible = await _visible_tools(middleware, _request(model, [static, provider_tool]))

    assert seen == ["first-integration", "second-integration"]
    assert visible == [static, provider_tool, second]


async def test_tools_resolve_once_route_execution_and_report_unavailable() -> None:
    builds = 0
    available = _tool("available")

    async def load() -> list[BaseTool]:
        nonlocal builds
        builds += 1
        return [available]

    middleware = DynamicToolMiddleware(
        {"MCP": IntegrationGroup(tool_names=("available", "unavailable"), load=load)}
    )
    request = _request(ChatOpenAI(model="gpt-5.5", api_key="test"))
    await _visible_tools(middleware, request)
    await _visible_tools(middleware, request)
    assert builds == 1

    handler = AsyncMock(return_value=ToolMessage(content="ok", tool_call_id="call-1"))
    await middleware.awrap_tool_call(_tool_request("available"), handler)
    assert handler.await_args.args[0].tool is available

    result = await middleware.awrap_tool_call(_tool_request("unavailable"), handler)
    assert isinstance(result, ToolMessage)
    assert result.status == "error"
    assert "unavailable right now" in result.content
    assert handler.await_count == 1


async def test_group_load_failure_is_graceful() -> None:
    async def load() -> list[BaseTool]:
        raise RuntimeError("MCP unavailable")

    middleware = DynamicToolMiddleware(
        {"MCP": IntegrationGroup(tool_names=("integration",), load=load)}
    )
    request = _request(ChatOpenAI(model="gpt-5.5", api_key="test"))

    assert await _visible_tools(middleware, request) == []
    result = await middleware.awrap_tool_call(_tool_request("integration"), AsyncMock())
    assert isinstance(result, ToolMessage)
    assert result.status == "error"


def test_catalog_and_resolved_collisions_are_rejected() -> None:
    with pytest.raises(ValueError, match="Duplicate integration tool name: static"):
        DynamicToolMiddleware({"MCP": [_tool("static")]}, reserved_names={"static"})
    with pytest.raises(ValueError, match="Duplicate integration tool name: same"):
        DynamicToolMiddleware({"A": [_tool("same")], "B": [_tool("same")]})


async def test_unexpected_resolved_tool_collision_is_rejected() -> None:
    async def load_first() -> list[BaseTool]:
        return [_tool("first"), _tool("second")]

    async def load_second() -> list[BaseTool]:
        return [_tool("second")]

    middleware = DynamicToolMiddleware(
        {
            "First": IntegrationGroup(tool_names=("first",), load=load_first),
            "Second": IntegrationGroup(tool_names=("second",), load=load_second),
        }
    )

    with pytest.raises(ValueError, match="Duplicate integration tool name: second"):
        await _visible_tools(middleware, _request(ChatOpenAI(model="gpt-5.5", api_key="test")))


class _ToolCallingFakeModel(FakeMessagesListChatModel):
    def bind_tools(
        self,
        tools: Sequence[object],
        *,
        tool_choice: str | None = None,
        **kwargs: object,
    ) -> Self:
        return self


async def test_create_agent_fallback_search_executes_integration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    async def integration(value: str = "") -> str:
        calls.append(value)
        return f"integration:{value}"

    tool = StructuredTool.from_function(
        coroutine=integration,
        name="integration",
        description="Use integration.",
    )
    model = _ToolCallingFakeModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[{"name": "integration", "args": {"value": "result"}, "id": "call-1"}],
            ),
            AIMessage(content="done"),
        ]
    )

    async def select(
        _self: LLMToolSelectorMiddleware,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        return await handler(request.override(tools=[tool]))

    monkeypatch.setattr(LLMToolSelectorMiddleware, "awrap_model_call", select)
    graph = create_agent(model=model, middleware=[DynamicToolMiddleware({"MCP": [tool]})])

    result = await graph.ainvoke({"messages": [HumanMessage("use the integration")]})

    assert calls == ["result"]
    assert result["messages"][-1].content == "done"
    tool_messages = [message for message in result["messages"] if isinstance(message, ToolMessage)]
    assert tool_messages[-1].content == "integration:result"


def test_general_purpose_subagent_includes_dynamic_tools() -> None:
    from agent.server import _general_purpose_subagent

    middleware = DynamicToolMiddleware({"MCP": [_tool("integration")]})
    subagent = _general_purpose_subagent(MagicMock(), tools=[], dynamic_tools=middleware)

    assert middleware in subagent.get("middleware", [])
