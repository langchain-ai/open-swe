"""The model sees two tools; everything downstream sees the real ones."""

import asyncio
from dataclasses import dataclass, replace
from typing import Any, cast

import pytest
from langchain.agents.middleware.types import ModelRequest, ModelResponse, ToolCallRequest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import BaseTool, StructuredTool
from langgraph.types import Command

from agent.middleware.tool_search import (
    _SUMMARY_CHARS,
    TOOL_DESCRIBE_NAME,
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

    assert [tool.name for tool in middleware.tools] == [
        TOOL_SEARCH_NAME,
        TOOL_DESCRIBE_NAME,
        TOOL_INVOKE_NAME,
    ]
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


async def test_a_lone_match_comes_back_ready_to_run() -> None:
    middleware = _middleware()
    search = cast(Any, middleware.tools[0]).coroutine

    command = await search(query="emoji reaction", limit=5, tool_call_id="s1")

    assert isinstance(command, Command)
    update = cast(dict[str, Any], command.update)
    body = update["messages"][0].content
    assert "slack_add_reaction" in body
    assert "parameters:" in body
    assert update["searched_tools"] == ["slack_add_reaction"]


async def test_several_matches_come_back_one_line_each() -> None:
    middleware = _middleware()
    search = cast(Any, middleware.tools[0]).coroutine

    command = await search(query="slack", limit=5, tool_call_id="s1")

    body = cast(dict[str, Any], command.update)["messages"][0].content
    assert "- slack_thread_reply: Reply in the Slack thread" in body
    assert "- slack_add_reaction: Add an emoji reaction to a message" in body
    # Choosing does not need the parameters yet.
    assert "parameters:" not in body
    assert TOOL_DESCRIBE_NAME in body


async def test_a_summary_stops_at_the_first_paragraph_and_200_characters() -> None:
    long = "First line.\n" + "x" * 40 + "\n\nSecond paragraph should not appear."
    middleware = ToolSearchMiddleware({"Group": [_tool("wordy", long), _tool("other", "a" * 400)]})
    search = cast(Any, middleware.tools[0]).coroutine

    command = await search(query="wordy other", limit=5, tool_call_id="s1")

    body = cast(dict[str, Any], command.update)["messages"][0].content
    assert "Second paragraph" not in body
    for line in body.splitlines():
        if line.startswith("- "):
            assert len(line) <= len("- other: ") + _SUMMARY_CHARS + 1


async def test_describe_returns_the_full_entry_for_every_name_at_once() -> None:
    middleware = _middleware()
    describe = cast(Any, middleware.tools[1]).coroutine

    command = await describe(names=["slack_add_reaction", "linear_comment"], tool_call_id="d1")

    update = cast(dict[str, Any], command.update)
    body = update["messages"][0].content
    assert body.count("parameters:") == 2
    assert update["searched_tools"] == ["slack_add_reaction", "linear_comment"]


async def test_describe_names_what_it_could_not_find() -> None:
    middleware = _middleware()
    describe = cast(Any, middleware.tools[1]).coroutine

    command = await describe(names=["nope"], tool_call_id="d1")

    message = cast(dict[str, Any], command.update)["messages"][0]
    assert message.status == "error"
    assert "nope" in message.content


async def test_loading_starts_without_blocking_the_run() -> None:
    release = asyncio.Event()

    async def load() -> list[BaseTool]:
        await release.wait()
        return [_tool("notion-search", "Search Notion pages")]

    middleware = ToolSearchMiddleware(
        {"Notion": ToolGroup(tool_names=("notion-search",), load=load, summary="Notion pages")}
    )

    # Returns while the load is still outstanding, so the first model call is
    # not waiting on a handshake.
    await asyncio.wait_for(middleware.abefore_agent(cast(Any, {}), cast(Any, None)), timeout=1)
    assert not release.is_set()

    # A search does not wait either; the group is simply not there yet.
    assert await middleware._search("notion pages", 5) == []

    release.set()
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    matches = await middleware._search("notion pages", 5)
    assert [tool.name for tool in matches] == ["notion-search"]


async def test_a_loaded_description_is_searchable_without_naming_its_group() -> None:
    """The point of loading early: reach a tool whose name the query never says."""

    async def load() -> list[BaseTool]:
        return [_tool("notion-search", "Look up an internal knowledge base article.")]

    middleware = ToolSearchMiddleware(
        {"Notion": ToolGroup(tool_names=("notion-search",), load=load, summary="Notion pages")}
    )
    await middleware.abefore_agent(cast(Any, {}), cast(Any, None))
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    matches = await middleware._search("knowledge base article", 5)
    assert [tool.name for tool in matches] == ["notion-search"]


async def test_a_group_that_cannot_name_its_tools_registers_them_once_loaded() -> None:
    async def load() -> list[BaseTool]:
        return [_tool("datadog_search_logs", "Search Datadog logs for errors.")]

    middleware = ToolSearchMiddleware(
        {"Datadog": ToolGroup(tool_names=(), load=load, summary="Datadog metrics and logs")}
    )
    assert middleware.proxied_names == frozenset()

    await middleware.abefore_agent(cast(Any, {}), cast(Any, None))
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert middleware.proxied_names == {"datadog_search_logs"}
    assert [tool.name for tool in await middleware._search("datadog errors", 5)] == [
        "datadog_search_logs"
    ]


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
    # The result keeps the real tool's name: `tool_call_id` is what links it to
    # the call, and the name is provenance the model still reads.
    assert cast(ToolMessage, captured["messages"][1]).name == "slack_thread_reply"
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
async def test_an_empty_query_returns_nothing(query: str) -> None:
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


async def test_a_result_states_the_description_once() -> None:
    """Pydantic repeats the docstring in the schema; it was 44% of a result."""

    async def run(message: str) -> str:
        """Post a message to the current Slack thread."""
        return message

    middleware = ToolSearchMiddleware(
        {"Slack": [StructuredTool.from_function(coroutine=run, name="slack_thread_reply")]}
    )
    search = cast(Any, middleware.tools[0]).coroutine

    command = await search(query="slack thread", limit=5, tool_call_id="s1")
    body = cast(dict[str, Any], command.update)["messages"][0].content

    assert body.count("Post a message to the current Slack thread") == 1
    assert '"title"' not in body
