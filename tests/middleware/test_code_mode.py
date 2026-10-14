"""Code mode can be enabled while a thread is stopped at a pending tool call."""

from collections.abc import Awaitable, Callable
from typing import cast

import pytest
from langchain.agents import create_agent
from langchain.agents.middleware import wrap_model_call
from langchain.agents.middleware.types import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool
from langchain_quickjs.middleware import REPLState
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.prebuilt.tool_node import ToolCallRequest, ToolRuntime
from langgraph.runtime import Runtime

from openswe.middleware.code_mode import ResumableCodeInterpreterMiddleware


@tool
async def lookup() -> str:
    """Look up an integration result."""
    return "integration result"


@pytest.mark.parametrize("initialized", [False, True])
async def test_model_call_initializes_only_a_missing_slot(initialized: bool) -> None:
    middleware = ResumableCodeInterpreterMiddleware(ptc=[lookup], subagents=False, mode="turn")
    state: REPLState = {"messages": []}
    runtime = Runtime()
    if initialized:
        state.update(await middleware.abefore_agent(state, runtime) or {})
    original_slot = state.get("_quickjs_slot_id")
    response = ModelResponse[object](result=[AIMessage(content="done")])
    observed: list[REPLState] = []

    async def model_handler(request: ModelRequest[None]) -> ModelResponse[object]:
        observed.append(cast(REPLState, request.state))
        assert request.system_message is not None
        assert request.system_message.text.startswith("original prompt")
        assert "lookup" in request.system_message.text
        assert {offered.name for offered in request.tools} == {"lookup", "eval"}
        return response

    request = ModelRequest(
        model=FakeListChatModel(responses=["unused"]),
        messages=[],
        system_message=SystemMessage(content="original prompt"),
        tools=[lookup, *middleware.tools],
        state=state,
        runtime=runtime,
    )
    assert await middleware.awrap_model_call(request, model_handler) is response
    assert observed[0]["_quickjs_slot_id"]
    if initialized:
        assert observed[0]["_quickjs_slot_id"] == original_slot
    await middleware.aafter_agent(observed[0], runtime)


async def test_eval_call_initializes_missing_runtime_state() -> None:
    middleware = ResumableCodeInterpreterMiddleware(subagents=False, mode="turn")
    state: REPLState = {"messages": []}
    runtime = ToolRuntime(
        state=state,
        context=None,
        config={},
        stream_writer=lambda event: None,
        tool_call_id="eval-call",
        store=None,
    )
    request = ToolCallRequest(
        tool_call={"name": "eval", "args": {"code": "21 * 2"}, "id": "eval-call"},
        tool=middleware.tools[0],
        state=state,
        runtime=runtime,
    )

    async def tool_handler(request: ToolCallRequest) -> ToolMessage:
        assert request.runtime.state == request.state
        assert request.tool is not None
        result = await request.tool.ainvoke(
            {**request.tool_call["args"], "runtime": request.runtime}
        )
        assert isinstance(result, ToolMessage)
        await middleware.aafter_agent(request.state, Runtime())
        return result

    result = await middleware.awrap_tool_call(request, tool_handler)
    assert isinstance(result, ToolMessage)
    assert "42" in result.content
    assert result.status == "success"


async def test_pending_tool_checkpoint_resumes_with_code_mode_enabled() -> None:
    checkpointer = InMemorySaver()
    config = {"configurable": {"thread_id": "enable-code-mode-on-resume"}}
    model = FakeListChatModel(responses=["unused"])

    @wrap_model_call(state_schema=REPLState)
    async def scripted_model(
        request: ModelRequest[None],
        handler: Callable[[ModelRequest[None]], Awaitable[ModelResponse[object]]],
    ) -> ModelResponse[object]:
        results = [message for message in request.messages if isinstance(message, ToolMessage)]
        if not results:
            return ModelResponse(
                result=[
                    AIMessage(
                        content="", tool_calls=[{"name": "lookup", "args": {}, "id": "lookup-call"}]
                    )
                ]
            )
        assert request.state["_quickjs_slot_id"]
        assert request.system_message is not None
        assert "lookup" in request.system_message.text
        if len(results) == 1:
            return ModelResponse(
                result=[
                    AIMessage(
                        content="",
                        tool_calls=[
                            {
                                "name": "eval",
                                "args": {"code": "await tools.lookup({})"},
                                "id": "eval-call",
                            }
                        ],
                    )
                ]
            )
        return ModelResponse(result=[AIMessage(content="done")])

    original = create_agent(
        model,
        tools=[lookup],
        middleware=[scripted_model],
        checkpointer=checkpointer,
        interrupt_before=["tools"],
    )
    await original.ainvoke({"messages": [HumanMessage(content="look it up")]}, config)
    checkpoint = await original.aget_state(config)
    assert checkpoint.next == ("tools",)
    assert "_quickjs_slot_id" not in checkpoint.values

    middleware = ResumableCodeInterpreterMiddleware(ptc=[lookup], subagents=False, mode="turn")
    resumed_middleware: list[AgentMiddleware[REPLState, None, object]] = [
        middleware,
        scripted_model,
    ]
    resumed = create_agent(
        model,
        tools=[],
        middleware=resumed_middleware,
        checkpointer=checkpointer,
    )
    result = await resumed.ainvoke(None, config)
    assert result["messages"][-1].content == "done"
    results = [message for message in result["messages"] if isinstance(message, ToolMessage)]
    assert [message.tool_call_id for message in results] == ["lookup-call", "eval-call"]
    assert all(message.status == "success" for message in results)
    assert all("integration result" in message.content for message in results)
    assert (await resumed.aget_state(config)).values["_quickjs_slot_id"]


async def test_cleanup_without_a_model_call_does_not_require_a_slot() -> None:
    middleware = ResumableCodeInterpreterMiddleware(subagents=False, mode="turn")
    assert await middleware.aafter_agent({"messages": []}, Runtime()) is None
