from typing import cast
from unittest.mock import AsyncMock

from langchain.agents.middleware.types import AgentState, ModelRequest, ModelResponse
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, ToolMessage

from agent.middleware.stable_tool_order import StableToolResultOrderMiddleware


def _ai(*call_ids: str) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[
            {"name": "execute", "args": {"command": "ls"}, "id": call_id, "type": "tool_call"}
            for call_id in call_ids
        ],
    )


def _result(call_id: str) -> ToolMessage:
    return ToolMessage(content=call_id, tool_call_id=call_id, name="execute")


async def _forward(messages: list[AnyMessage]) -> list[AnyMessage]:
    request = ModelRequest(
        model=AsyncMock(),
        messages=messages,
        state=cast(AgentState, {"messages": messages}),
    )
    handler = AsyncMock(return_value=ModelResponse(result=[AIMessage(content="ok")]))
    await StableToolResultOrderMiddleware().awrap_model_call(request, handler)
    assert handler.await_args is not None
    return handler.await_args.args[0].messages


async def test_sorts_parallel_results_into_tool_call_order() -> None:
    ai = _ai("call_a", "call_b", "call_c")
    forwarded = await _forward(
        [HumanMessage(content="go"), ai, _result("call_c"), _result("call_a"), _result("call_b")]
    )

    assert [getattr(message, "tool_call_id", None) for message in forwarded[2:]] == [
        "call_a",
        "call_b",
        "call_c",
    ]


async def test_orders_each_batch_against_its_own_tool_calls() -> None:
    first = _ai("call_a", "call_b")
    second = _ai("call_c", "call_d")
    forwarded = await _forward(
        [
            first,
            _result("call_b"),
            _result("call_a"),
            second,
            _result("call_d"),
            _result("call_c"),
        ]
    )

    assert [getattr(message, "tool_call_id", None) for message in forwarded] == [
        None,
        "call_a",
        "call_b",
        None,
        "call_c",
        "call_d",
    ]


async def test_leaves_already_ordered_history_untouched() -> None:
    ai = _ai("call_a", "call_b")
    messages = [ai, _result("call_a"), _result("call_b")]

    forwarded = await _forward(messages)

    assert forwarded is messages


async def test_keeps_unmatched_results_after_the_ordered_batch() -> None:
    ai = _ai("call_a", "call_b")
    orphan = _result("call_stale")
    forwarded = await _forward([ai, orphan, _result("call_b"), _result("call_a")])

    assert [getattr(message, "tool_call_id", None) for message in forwarded[1:]] == [
        "call_a",
        "call_b",
        "call_stale",
    ]


async def test_stops_the_batch_at_a_non_tool_message() -> None:
    ai = _ai("call_a", "call_b")
    interjection = HumanMessage(content="stop")
    forwarded = await _forward([ai, _result("call_b"), _result("call_a"), interjection])

    assert [getattr(message, "tool_call_id", None) for message in forwarded] == [
        None,
        "call_a",
        "call_b",
        None,
    ]
