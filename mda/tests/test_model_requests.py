from collections.abc import Callable
from copy import deepcopy
from typing import cast
from unittest.mock import AsyncMock

import pytest
from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import AgentState, ModelRequest, ModelResponse
from langchain_anthropic import ChatAnthropic
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, AnyMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.runnables import Runnable
from langchain_fireworks import ChatFireworks
from langchain_openai import ChatOpenAI
from pydantic import SecretStr
from rt.model_requests import SanitizeProviderMessagesMiddleware, StableToolResultOrderMiddleware


async def _forward(
    middleware: AgentMiddleware, messages: list[AnyMessage], model: object | None = None
) -> list[AnyMessage]:
    request = ModelRequest(
        model=cast(BaseChatModel, model or AsyncMock()),
        messages=messages,
        state=cast(AgentState, {"messages": messages}),
    )
    handler = AsyncMock(return_value=ModelResponse(result=[AIMessage(content="ok")]))
    await middleware.awrap_model_call(request, handler)
    assert handler.await_args is not None
    return handler.await_args.args[0].messages


def _openai() -> ChatOpenAI:
    return ChatOpenAI(
        model="gpt-5.6-sol",
        api_key=SecretStr("test"),
        use_responses_api=True,
        store=False,
        include=["reasoning.encrypted_content"],
        output_version="responses/v1",
    )


def _anthropic() -> ChatAnthropic:
    return ChatAnthropic(model_name="claude-opus-5-5", api_key=SecretStr("test"))


def _fireworks() -> ChatFireworks:
    return ChatFireworks(model="accounts/fireworks/models/kimi-k3", api_key=SecretStr("test"))


def _legacy_function_call() -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{"name": "read_file", "args": {"file_path": "/x"}, "id": "tc1"}],
        additional_kwargs={"function_call": {"name": "read_file", "arguments": "{}"}},
    )


def _unstored_reasoning() -> AIMessage:
    return AIMessage(
        content=[
            {"type": "reasoning", "id": "rs_unstored", "summary": [], "encrypted_content": None},
            {"type": "text", "text": "ok"},
        ]
    )


def _empty_thinking() -> AIMessage:
    return AIMessage(
        content=[
            {"type": "thinking", "signature": "abc", "thinking": ""},
            {"type": "text", "text": "ok"},
        ]
    )


@pytest.mark.parametrize(
    ("model", "fixed"),
    [(_fireworks, "function_call"), (_openai, "reasoning"), (_anthropic, "thinking")],
)
async def test_each_provider_gets_only_the_fix_it_needs(
    model: Callable[[], BaseChatModel], fixed: str
) -> None:
    history: list[AnyMessage] = [
        HumanMessage("hi"),
        _legacy_function_call(),
        _unstored_reasoning(),
        _empty_thinking(),
    ]
    original = deepcopy(history)
    bound: Runnable = model().bind(stop=["\n\n"])

    forwarded = await _forward(SanitizeProviderMessagesMiddleware(), history, bound)

    changed = {
        name
        for name, before, after in zip(
            ["function_call", "reasoning", "thinking"], original[1:], forwarded[1:], strict=True
        )
        if before != after
    }
    assert changed == {fixed}
    assert history == original


async def test_openai_replays_tool_history_without_unstored_reasoning() -> None:
    model = _openai()
    history: list[AnyMessage] = [
        HumanMessage("test the todo middleware"),
        AIMessage(
            content=[
                {
                    "type": "reasoning",
                    "id": "rs_unstored",
                    "summary": [],
                    "encrypted_content": None,
                },
                {
                    "type": "function_call",
                    "id": "fc_execute",
                    "call_id": "call_execute",
                    "name": "execute",
                    "arguments": '{"command":"pwd"}',
                },
            ],
            tool_calls=[
                {"name": "execute", "args": {"command": "pwd"}, "id": "call_execute"},
            ],
        ),
        ToolMessage(content="/workspace/open-swe", tool_call_id="call_execute", name="execute"),
        HumanMessage("continue"),
    ]

    forwarded = await _forward(SanitizeProviderMessagesMiddleware(), history, model)
    payload = model._get_request_payload(forwarded)

    assert not any(item.get("id") == "rs_unstored" for item in payload["input"])
    assert {
        "type": "function_call_output",
        "output": "/workspace/open-swe",
        "call_id": "call_execute",
    } in payload["input"]


async def test_fireworks_sends_tool_calls_without_the_legacy_function_call() -> None:
    model = _fireworks()

    forwarded = await _forward(
        SanitizeProviderMessagesMiddleware(), [HumanMessage("hi"), _legacy_function_call()], model
    )
    messages: list[BaseMessage] = [*forwarded]
    sent, _ = model._create_message_dicts(messages, None)

    assert all("function_call" not in message for message in sent)
    assert "tool_calls" in sent[1]


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


def _call_ids(messages: list[AnyMessage]) -> list[str | None]:
    return [getattr(message, "tool_call_id", None) for message in messages]


@pytest.mark.parametrize(
    ("history", "ordered"),
    [
        (
            [_ai("a", "b", "c"), _result("c"), _result("a"), _result("b")],
            [None, "a", "b", "c"],
        ),
        (
            [_ai("a", "b"), _result("b"), _result("a"), _ai("c", "d"), _result("d"), _result("c")],
            [None, "a", "b", None, "c", "d"],
        ),
        (
            [_ai("a", "b"), _result("stale"), _result("b"), _result("a")],
            [None, "a", "b", "stale"],
        ),
        (
            [_ai("a", "b"), _result("b"), _result("a"), HumanMessage("stop"), _result("z")],
            [None, "a", "b", None, "z"],
        ),
    ],
)
async def test_parallel_tool_results_reach_the_model_in_call_order(
    history: list[AnyMessage], ordered: list[str | None]
) -> None:
    forwarded = await _forward(StableToolResultOrderMiddleware(), history)

    assert _call_ids(forwarded) == ordered


async def test_an_already_ordered_history_is_sent_as_it_is() -> None:
    history: list[AnyMessage] = [_ai("a", "b"), _result("a"), _result("b")]

    assert await _forward(StableToolResultOrderMiddleware(), history) is history
