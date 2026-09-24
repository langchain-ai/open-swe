import warnings
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from itertools import pairwise
from typing import Any, cast
from unittest.mock import MagicMock

import pytest
from langchain.agents.middleware.types import ModelRequest, ModelResponse, ToolCallRequest
from langchain_anthropic import ChatAnthropic
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.runnables import RunnableBinding
from langchain_core.tools import BaseTool, StructuredTool
from langgraph.runtime import Runtime
from langgraph.types import Command, Overwrite
from pydantic import SecretStr

from agent.middleware.dynamic_tools import DynamicToolMiddleware, DynamicToolState, IntegrationGroup


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
    messages: list[AnyMessage] = field(default_factory=list)
    model: object = None

    def override(self, **kwargs: Any) -> _Request:
        return replace(self, **kwargs)


def _opus() -> ChatAnthropic:
    return ChatAnthropic(model_name="claude-opus-5-5", api_key=SecretStr("test"))


def _notion() -> DynamicToolMiddleware:
    return DynamicToolMiddleware(
        {"Notion": [_tool("notion-search"), _tool("notion-update-page"), _tool("notion-fetch")]}
    )


@dataclass
class _Thread:
    """Drives the agent loop around the middleware and records every model request."""

    middleware: DynamicToolMiddleware
    model: object
    messages: list[AnyMessage] = field(default_factory=lambda: [HumanMessage("Find the plan.")])
    loaded: list[str] = field(default_factory=list)
    requests: list[_Request] = field(default_factory=list)
    static_tools: list[BaseTool] = field(default_factory=lambda: [_tool("execute")])

    async def model_call(self) -> _Request:
        async def handler(request: ModelRequest) -> ModelResponse:
            self.requests.append(cast(_Request, request))
            return ModelResponse(result=[])

        request = _Request(
            state={"messages": self.messages, "loaded_integration_tools": self.loaded},
            tools=self.static_tools,
            messages=list(self.messages),
            model=self.model,
        )
        await self.middleware.awrap_model_call(cast(ModelRequest, request), handler)
        return self.requests[-1]

    async def tool_turn(self, *calls: list[str] | str) -> None:
        """Answer one AI message of parallel calls: a list loads those tools, a str calls one."""
        start = len(self.messages)
        tool_calls = [
            {
                "name": "load_integration_tools" if isinstance(call, list) else call,
                "args": {"tool_names": call} if isinstance(call, list) else {"value": "x"},
                "id": f"call-{start}-{position}",
                "type": "tool_call",
            }
            for position, call in enumerate(calls)
        ]
        self.messages.append(AIMessage("", tool_calls=tool_calls))
        loader = cast(
            Callable[..., Awaitable[Command]],
            cast(StructuredTool, self.middleware.tools[0]).coroutine,
        )
        # Parallel calls all see the state from before the batch.
        state = {"messages": list(self.messages), "loaded_integration_tools": list(self.loaded)}
        loaded = set(self.loaded)
        for call, tool_call in zip(calls, tool_calls, strict=True):
            if isinstance(call, str):
                self.messages.append(ToolMessage("ok", tool_call_id=tool_call["id"]))
                continue
            command = await loader(tool_names=call, state=state, tool_call_id=tool_call["id"])
            update = command.update
            assert isinstance(update, dict)
            self.messages.extend(update["messages"])
            loaded.update(update.get("loaded_integration_tools", []))
        self.loaded = sorted(loaded)

    async def new_run(self, prompt: str) -> None:
        state = cast(DynamicToolState, {"messages": self.messages})
        update = await self.middleware.abefore_agent(state, cast(Runtime, MagicMock()))
        reset = update["loaded_integration_tools"]
        self.loaded = list(reset.value) if isinstance(reset, Overwrite) else reset
        self.messages.append(HumanMessage(prompt))


_SYSTEM_PROMPT = "You are Open SWE."


def _payload(request: _Request) -> dict[str, Any]:
    """Build the request the provider would be sent, failing on any warning it raises."""
    model = cast(ChatAnthropic, request.model)
    binding = model.bind_tools(request.tools)
    assert isinstance(binding, RunnableBinding)
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        return model._get_request_payload(
            [SystemMessage(_SYSTEM_PROMPT), *request.messages], **binding.kwargs
        )


def _anthropic_turns(payload: dict[str, Any]) -> list[str]:
    """Each turn's role, with the tools a mid-conversation system turn adds."""
    return [
        " ".join(["system", *(block["tool"]["definition"]["name"] for block in turn["content"])])
        if turn["role"] == "system"
        else turn["role"]
        for turn in payload["messages"]
    ]


def _added(request: _Request) -> list[dict[str, Any]]:
    """The tool definitions the provider request adds mid-conversation, in order."""
    return [
        block["tool"]["definition"]
        for turn in _payload(request)["messages"]
        if turn["role"] == "system"
        for block in turn["content"]
    ]


def _offered(request: _Request) -> list[str]:
    """The tool names the provider request exposes, in ``tools`` or added mid-conversation."""
    in_tools = [tool["name"] for tool in _payload(request)["tools"]]
    return in_tools + [tool["name"] for tool in _added(request)]


def _shape(messages: list[AnyMessage]) -> list[str]:
    """Each message's type, with an addition written as the tools it adds."""
    shape: list[str] = []
    for message in messages:
        if not isinstance(message, SystemMessage):
            shape.append(message.type)
            continue
        shape.append(
            " ".join(
                f"+{block['tool']['definition']['name']}"
                for block in message.content
                if isinstance(block, dict)
            )
        )
    return shape


@pytest.mark.parametrize("model", [pytest.param(_opus(), id="anthropic")])
async def test_loading_a_tool_only_appends_to_the_request_on_a_supported_model(
    model: object,
) -> None:
    thread = _Thread(_notion(), model)

    await thread.model_call()
    await thread.tool_turn(["notion-search"])
    await thread.model_call()
    await thread.tool_turn("notion-search")
    await thread.model_call()
    await thread.tool_turn(["notion-search", "notion-fetch"])
    await thread.model_call()
    await thread.tool_turn("notion-fetch")
    await thread.model_call()

    for previous, current in pairwise(thread.requests):
        assert current.messages[: len(previous.messages)] == previous.messages
        assert current.tools == previous.tools
    assert _offered(thread.requests[0]) == ["execute"]
    assert _offered(thread.requests[1]) == ["execute", "notion-search"]
    assert _offered(thread.requests[-1]) == ["execute", "notion-search", "notion-fetch"]


@pytest.mark.parametrize("model", [pytest.param(_opus(), id="anthropic")])
async def test_an_added_tool_is_defined_as_it_would_be_in_tools(model: object) -> None:
    added = _Thread(_notion(), model)
    await added.tool_turn(["notion-search"])
    # Loaded, but with no load result to anchor to, so it goes to ``tools``.
    in_tools = _Thread(_notion(), model, loaded=["notion-search"])

    definitions = _added(await added.model_call())

    assert definitions == [_payload(await in_tools.model_call())["tools"][-1]]


async def test_tools_loaded_in_a_parallel_batch_are_added_together_after_it() -> None:
    thread = _Thread(_notion(), _opus())

    await thread.tool_turn(["notion-update-page"], "execute", ["notion-search"])
    request = await thread.model_call()

    assert _shape(request.messages) == [
        "human",
        "ai",
        "tool",
        "tool",
        "tool",
        "+notion-search +notion-update-page",
    ]


async def test_a_tool_loaded_as_a_follow_up_arrives_is_added_after_the_follow_up() -> None:
    thread = _Thread(_notion(), _opus())

    await thread.tool_turn(["notion-search"])
    thread.messages.append(HumanMessage("Check the archive too."))
    await thread.model_call()
    await thread.tool_turn("notion-search")
    request = await thread.model_call()

    assert _shape(request.messages) == [
        "human",
        "ai",
        "tool",
        "human",
        "+notion-search",
        "ai",
        "tool",
    ]


async def test_reloading_a_loaded_tool_adds_only_the_tools_new_to_this_run() -> None:
    thread = _Thread(_notion(), _opus())

    await thread.tool_turn(["notion-search"])
    await thread.tool_turn(["notion-search"])
    await thread.tool_turn(["notion-search", "notion-fetch"])
    request = await thread.model_call()

    assert _shape(request.messages) == [
        "human",
        "ai",
        "tool",
        "+notion-search",
        "ai",
        "tool",
        "ai",
        "tool",
        "+notion-fetch",
    ]


async def test_a_tool_loaded_again_in_a_later_run_is_added_at_the_new_load() -> None:
    thread = _Thread(_notion(), _opus())
    await thread.tool_turn(["notion-search"])
    thread.messages.append(AIMessage("Found it."))

    await thread.new_run("Now update it.")
    before_reload = await thread.model_call()
    await thread.tool_turn(["notion-search"])
    after_reload = await thread.model_call()

    assert _offered(before_reload) == ["execute"]
    assert _shape(before_reload.messages) == ["human", "ai", "tool", "ai", "human"]
    assert _shape(after_reload.messages) == [
        "human",
        "ai",
        "tool",
        "ai",
        "human",
        "ai",
        "tool",
        "+notion-search",
    ]


@pytest.mark.parametrize(
    "model",
    [
        pytest.param(
            ChatAnthropic(model_name="claude-sonnet-5", api_key=SecretStr("test")),
            id="claude-sonnet-5",
        ),
        pytest.param(GenericFakeChatModel(messages=iter([])), id="other-provider"),
    ],
)
async def test_unsupported_models_receive_loaded_tools_in_tools(model: object) -> None:
    thread = _Thread(_notion(), model)

    await thread.tool_turn(["notion-search"])
    request = await thread.model_call()

    assert request.messages == thread.messages
    assert [tool.name for tool in request.tools] == ["execute", "notion-search"]


async def test_a_tool_claude_rejects_inline_keeps_todays_path() -> None:
    async def run(**_: str) -> str:
        return "ok"

    # Anthropic rejects a root anyOf; ``bind_tools`` drops such a tool from ``tools``,
    # but inline it would fail every request for the rest of the run.
    either = {"anyOf": [{"required": ["page_id"]}, {"required": ["url"]}]}
    schema = {"type": "object", "properties": {"page_id": {}, "url": {}}, **either}
    fetch = StructuredTool.from_function(
        coroutine=run, name="notion-fetch", description="Fetch a page.", args_schema=schema
    )
    thread = _Thread(DynamicToolMiddleware({"Notion": [_tool("notion-search"), fetch]}), _opus())

    await thread.tool_turn(["notion-search", "notion-fetch"])
    request = await thread.model_call()

    assert _shape(request.messages) == ["human", "ai", "tool", "+notion-search"]
    assert [tool.name for tool in request.tools] == ["execute", "notion-fetch"]


async def test_a_loaded_tool_without_a_visible_load_result_goes_to_tools() -> None:
    thread = _Thread(_notion(), _opus())
    # notion-fetch's load was compacted away; notion-update-page's predates additions.
    thread.loaded = ["notion-fetch", "notion-update-page"]
    thread.messages += [
        AIMessage(
            "",
            tool_calls=[
                {
                    "name": "load_integration_tools",
                    "args": {"tool_names": ["notion-update-page"]},
                    "id": "old-load",
                    "type": "tool_call",
                }
            ],
        ),
        ToolMessage(
            "Loaded integration tool schemas: notion-update-page.", tool_call_id="old-load"
        ),
    ]

    await thread.tool_turn(["notion-search"])
    request = await thread.model_call()

    assert _shape(request.messages) == ["human", "ai", "tool", "ai", "tool", "+notion-search"]
    assert [tool.name for tool in request.tools] == [
        "execute",
        "notion-fetch",
        "notion-update-page",
    ]


async def _load_twice(model: object) -> _Request:
    """Load alongside a tool call as a follow-up arrives, then load again in a later batch."""
    thread = _Thread(_notion(), model)
    await thread.tool_turn(["notion-search"], "execute")
    thread.messages.append(HumanMessage("Check the archive too."))
    await thread.model_call()
    await thread.tool_turn("notion-search", ["notion-fetch"])
    return await thread.model_call()


async def test_anthropic_sends_additions_in_place() -> None:
    payload = _payload(await _load_twice(_opus()))

    assert payload["system"] == _SYSTEM_PROMPT
    assert _anthropic_turns(payload) == [
        "user",
        "assistant",
        "user",
        "system notion-search",
        "assistant",
        "user",
        "system notion-fetch",
    ]
    assert [tool["name"] for tool in payload["tools"]] == ["execute"]
    assert "inline-tools-2026-09-15" in payload["betas"]


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


async def test_a_group_whose_catalog_is_empty_is_not_offered() -> None:
    async def load() -> list[BaseTool]:
        return []

    middleware = DynamicToolMiddleware({"Corridor": IntegrationGroup(tool_names=(), load=load)})

    assert not middleware.has_groups
    assert "- Corridor" not in cast(StructuredTool, middleware.tools[0]).description


async def test_fork_preserves_loaded_integration_schemas() -> None:
    middleware = DynamicToolMiddleware({"Notion": [_tool("notion-search")]})
    state = cast(
        DynamicToolState,
        {
            "messages": [],
            "_deepagents_forked_context": True,
            "loaded_integration_tools": ["notion-search"],
        },
    )
    assert await middleware.abefore_agent(state, cast(Runtime, MagicMock())) == {}
