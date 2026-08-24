"""The model sees two tools; everything downstream sees the real ones."""

from dataclasses import dataclass, replace
from typing import Any, cast

import pytest
from langchain.agents.middleware.types import ModelRequest, ModelResponse, ToolCallRequest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import BaseTool, StructuredTool
from langgraph.types import Command

from agent.middleware.tool_search import (
    TOOL_INVOKE_NAME,
    TOOL_SEARCH_NAME,
    ToolGroup,
    ToolSearchMiddleware,
)


def _tool(name: str, description: str = "does a thing") -> BaseTool:
    async def run(value: str = "") -> str:
        return value

    return StructuredTool.from_function(coroutine=run, name=name, description=description)


@dataclass
class _Request:
    state: dict[str, Any]
    tools: list[BaseTool]
    messages: list[Any]
    tool_call: dict[str, Any] | None = None
    tool: BaseTool | None = None

    def override(self, **kwargs: Any) -> "_Request":
        return replace(self, **kwargs)


def _middleware(**kwargs: Any) -> ToolSearchMiddleware:
    return ToolSearchMiddleware(
        {
            "Slack": [
                _tool("slack_thread_reply", "Reply in the Slack thread"),
                _tool("slack_add_reaction", "Add an emoji reaction to a message"),
            ],
            "Linear": [_tool("linear_comment", "Comment on a Linear issue")],
        },
        **kwargs,
    )


def test_the_model_only_ever_sees_search_and_invoke() -> None:
    middleware = _middleware()

    assert [tool.name for tool in middleware.tools] == [TOOL_SEARCH_NAME, TOOL_INVOKE_NAME]
    assert middleware.proxied_names == {
        "slack_thread_reply",
        "slack_add_reaction",
        "linear_comment",
    }


async def test_search_matches_the_description_not_only_the_name() -> None:
    middleware = _middleware()

    by_description = await middleware._search("emoji reaction", 5)
    assert [tool.name for tool in by_description] == ["slack_add_reaction"]

    by_name = await middleware._search("linear_comment", 5)
    assert [tool.name for tool in by_name] == ["linear_comment"]


async def test_search_returns_the_parameters_the_model_needs_to_call() -> None:
    middleware = _middleware()
    search = cast(Any, middleware.tools[0]).coroutine

    command = await search(query="emoji reaction", limit=5, tool_call_id="s1")

    assert isinstance(command, Command)
    update = cast(dict[str, Any], command.update)
    body = update["messages"][0].content
    assert "slack_add_reaction" in body
    assert "parameters:" in body
    assert update["searched_tools"] == ["slack_add_reaction"]


async def test_a_group_needing_a_remote_call_waits_for_a_query_that_names_it() -> None:
    builds = 0

    async def load() -> list[BaseTool]:
        nonlocal builds
        builds += 1
        return [_tool("notion-search", "Search Notion pages")]

    middleware = ToolSearchMiddleware(
        {
            "Slack": [_tool("slack_thread_reply", "Reply in the Slack thread")],
            "Notion": ToolGroup(tool_names=("notion-search",), load=load, summary="Notion pages"),
        }
    )

    await middleware._search("slack reply", 5)
    assert builds == 0

    matches = await middleware._search("notion", 5)
    assert builds == 1
    assert [tool.name for tool in matches] == ["notion-search"]


async def _forward(middleware: ToolSearchMiddleware, request: _Request, response: ModelResponse):
    captured: dict[str, Any] = {}

    async def handler(req: ModelRequest) -> ModelResponse:
        captured["tools"] = [getattr(tool, "name", None) for tool in req.tools]
        captured["messages"] = req.messages
        return response

    result = await middleware.awrap_model_call(cast(ModelRequest, request), handler)
    return captured, result


async def test_proxied_tools_are_stripped_from_the_array_sent_to_the_model() -> None:
    middleware = _middleware(always_visible={"execute"})
    request = _Request(
        state={},
        tools=[_tool("execute", "run a command"), _tool("slack_thread_reply", "reply")],
        messages=[HumanMessage(content="hi")],
    )

    captured, _ = await _forward(middleware, request, ModelResponse(result=[AIMessage(content="")]))

    assert captured["tools"] == ["execute"]


async def test_an_invoke_call_becomes_a_real_tool_call_before_it_reaches_state() -> None:
    middleware = _middleware()
    invoked = AIMessage(
        content="",
        tool_calls=[
            {
                "name": TOOL_INVOKE_NAME,
                "args": {"name": "slack_thread_reply", "arguments": {"value": "hi"}},
                "id": "c1",
                "type": "tool_call",
            }
        ],
    )
    request = _Request(state={}, tools=[], messages=[HumanMessage(content="hi")])

    _, response = await _forward(middleware, request, ModelResponse(result=[invoked]))

    call = cast(AIMessage, response.result[0]).tool_calls[0]
    assert call["name"] == "slack_thread_reply"
    assert call["args"] == {"value": "hi"}
    assert call["id"] == "c1"


async def test_history_is_collapsed_so_the_wire_only_names_declared_tools() -> None:
    middleware = _middleware()
    history = [
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "slack_thread_reply",
                    "args": {"value": "earlier"},
                    "id": "c0",
                    "type": "tool_call",
                }
            ],
        ),
        ToolMessage(content="ok", tool_call_id="c0", name="slack_thread_reply"),
    ]
    request = _Request(state={}, tools=[], messages=history)

    captured, _ = await _forward(middleware, request, ModelResponse(result=[AIMessage(content="")]))

    sent_ai = cast(AIMessage, captured["messages"][0])
    assert sent_ai.tool_calls[0]["name"] == TOOL_INVOKE_NAME
    assert sent_ai.tool_calls[0]["args"] == {
        "name": "slack_thread_reply",
        "arguments": {"value": "earlier"},
    }
    assert cast(ToolMessage, captured["messages"][1]).name == TOOL_INVOKE_NAME
    # State keeps the real call; only the wire is collapsed.
    assert cast(AIMessage, history[0]).tool_calls[0]["name"] == "slack_thread_reply"


async def test_the_tools_array_is_identical_before_and_after_a_search() -> None:
    middleware = _middleware(always_visible={"execute"})
    tools = [_tool("execute", "run a command")]

    first, _ = await _forward(
        middleware,
        _Request(state={}, tools=tools, messages=[HumanMessage(content="hi")]),
        ModelResponse(result=[AIMessage(content="")]),
    )
    await middleware._search("emoji reaction", 5)
    second, _ = await _forward(
        middleware,
        _Request(
            state={"searched_tools": ["slack_add_reaction"]},
            tools=tools,
            messages=[HumanMessage(content="hi")],
        ),
        ModelResponse(result=[AIMessage(content="")]),
    )

    assert first["tools"] == second["tools"]


async def test_a_proxied_call_is_routed_to_the_real_tool() -> None:
    middleware = _middleware()
    routed: list[str] = []

    async def handler(request: ToolCallRequest) -> ToolMessage:
        assert request.tool is not None
        routed.append(request.tool.name)
        return ToolMessage(content="ok", tool_call_id=request.tool_call["id"])

    call = _Request(
        state={},
        tools=[],
        messages=[],
        tool_call={"name": "slack_thread_reply", "args": {"value": "x"}, "id": "c1"},
    )
    await middleware.awrap_tool_call(cast(ToolCallRequest, call), handler)

    assert routed == ["slack_thread_reply"]


@pytest.mark.parametrize("query", ["", "   "])
async def test_an_empty_query_never_triggers_a_remote_load(query: str) -> None:
    builds = 0

    async def load() -> list[BaseTool]:
        nonlocal builds
        builds += 1
        return [_tool("notion-search", "Search Notion pages")]

    middleware = ToolSearchMiddleware(
        {"Notion": ToolGroup(tool_names=("notion-search",), load=load)}
    )

    assert await middleware._search(query, 5) == []
    assert builds == 0
