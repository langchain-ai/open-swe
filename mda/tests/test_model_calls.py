import asyncio
from collections.abc import Awaitable, Callable
from copy import deepcopy
from typing import Protocol, cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain.agents.middleware.types import AgentState, ModelRequest, ModelResponse
from langchain_anthropic import ChatAnthropic
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    AnyMessage,
    BaseMessage,
    HumanMessage,
    ToolMessage,
)
from langchain_fireworks import ChatFireworks
from langchain_openai import ChatOpenAI
from pydantic import SecretStr
from rt.middleware import BackendRunMiddleware, ModelCall
from rt.model_calls import (
    ModelCallTimeoutError,
    ModelCallTimeoutMiddleware,
    ReplayableHistoryMiddleware,
)
from rt.models import ModelSpec

_ANTHROPIC = ChatAnthropic(model_name="claude-opus-5-5", api_key=SecretStr("test"))
_OPENAI = ChatOpenAI(
    model="gpt-6.1-sol",
    api_key=SecretStr("test"),
    use_responses_api=True,
    store=False,
    include=["reasoning.encrypted_content"],
    output_version="responses/v1",
)
_FIREWORKS = ChatFireworks(model="accounts/fireworks/models/kimi-k3", api_key=SecretStr("test"))


class _WrapsModelCalls(Protocol):
    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse: ...


async def _forwarded(
    middleware: _WrapsModelCalls,
    messages: list[AnyMessage],
    model: BaseChatModel,
    state: dict[str, object] | None = None,
) -> ModelRequest:
    request = ModelRequest(
        model=model,
        messages=messages,
        state=cast(AgentState, state or {"messages": messages}),
    )
    handler = AsyncMock(return_value=ModelResponse(result=[AIMessage(content="ok")]))
    await middleware.awrap_model_call(request, handler)
    assert handler.await_args is not None
    return handler.await_args.args[0]


def _calls(*call_ids: str) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[
            {"name": "execute", "args": {"command": "ls"}, "id": call_id, "type": "tool_call"}
            for call_id in call_ids
        ],
    )


def _result(call_id: str) -> ToolMessage:
    return ToolMessage(content=call_id, tool_call_id=call_id, name="execute")


async def test_parallel_results_are_replayed_in_tool_call_order() -> None:
    messages: list[AnyMessage] = [
        HumanMessage(content="go"),
        _calls("a", "b", "c"),
        _result("c"),
        _result("a"),
        _result("b"),
        HumanMessage(content="next"),
    ]
    original = list(messages)

    forwarded = await _forwarded(ReplayableHistoryMiddleware(), messages, _ANTHROPIC)

    assert [getattr(m, "tool_call_id", None) for m in forwarded.messages[2:5]] == ["a", "b", "c"]
    assert forwarded.messages[5] is messages[5]
    assert messages == original


async def test_empty_thinking_blocks_are_dropped_only_for_anthropic() -> None:
    reply = AIMessage(
        content=[
            {"type": "thinking", "signature": "s", "thinking": ""},
            {"type": "thinking", "signature": "t", "thinking": "kept"},
            {"type": "text", "text": "ok"},
        ]
    )
    original = deepcopy(reply)

    anthropic = await _forwarded(ReplayableHistoryMiddleware(), [reply], _ANTHROPIC)
    openai = await _forwarded(ReplayableHistoryMiddleware(), [reply], _OPENAI)

    assert anthropic.messages[0].content == [
        {"type": "thinking", "signature": "t", "thinking": "kept"},
        {"type": "text", "text": "ok"},
    ]
    assert openai.messages[0] is reply
    assert reply == original


async def test_stateless_openai_replay_drops_unresolvable_reasoning() -> None:
    messages: list[AnyMessage] = [
        HumanMessage("run pwd"),
        AIMessage(
            content=[
                {
                    "type": "reasoning",
                    "id": "rs_unstored",
                    "summary": [],
                    "encrypted_content": None,
                },
                {"type": "reasoning", "id": "rs_kept", "summary": [], "encrypted_content": "enc"},
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
        ToolMessage(content="/workspace", tool_call_id="call_execute", name="execute"),
    ]

    forwarded = await _forwarded(ReplayableHistoryMiddleware(), messages, _OPENAI)
    payload = _OPENAI._get_request_payload(forwarded.messages)

    ids = [item.get("id") for item in payload["input"]]
    assert "rs_unstored" not in ids
    assert "rs_kept" in ids
    assert "fc_execute" in ids


async def test_fireworks_replay_drops_the_legacy_function_call() -> None:
    # The gateway rejects it: "Extra inputs are not permitted, field: 'messages[N].function_call'".
    reply = AIMessage(
        content="",
        tool_calls=[{"name": "read_file", "args": {"file_path": "/x"}, "id": "tc1"}],
        additional_kwargs={"function_call": {"name": "read_file", "arguments": "{}"}},
    )

    forwarded = await _forwarded(
        ReplayableHistoryMiddleware(), [HumanMessage("hi"), reply], _FIREWORKS
    )
    replayed: list[BaseMessage] = list(forwarded.messages)
    sent, _ = _FIREWORKS._create_message_dicts(replayed, None)

    assert "function_call" not in sent[1]
    assert sent[1]["tool_calls"]
    assert "function_call" in reply.additional_kwargs


async def test_a_hanging_model_call_fails_as_a_timeout() -> None:
    async def hang(_request: ModelRequest) -> ModelResponse:
        await asyncio.sleep(60)
        raise AssertionError("cancelled by the deadline")

    with pytest.raises(ModelCallTimeoutError) as raised:
        await ModelCallTimeoutMiddleware(timeout_seconds=0.01).awrap_model_call(
            cast(ModelRequest, MagicMock()), hang
        )
    # The retry and the fallback both act on TimeoutError.
    assert isinstance(raised.value, TimeoutError)


@pytest.mark.parametrize("with_image", [True, False])
async def test_a_text_only_model_hands_images_to_the_vision_model(
    monkeypatch: pytest.MonkeyPatch, with_image: bool
) -> None:
    built: dict[str, BaseChatModel] = {"fireworks:kimi": _FIREWORKS, "openai:gpt": _OPENAI}
    monkeypatch.setattr(
        "rt.middleware.build_model", lambda spec, *, use_gateway: built[spec.model_id]
    )
    call = ModelCall(
        system_prompt="prompt",
        model=ModelSpec(model_id="fireworks:kimi", kwargs={}),
        vision=ModelSpec(model_id="openai:gpt", kwargs={}),
        fallback=None,
        use_gateway=False,
    )
    image = [{"type": "image", "url": "https://example.com/screenshot.png"}]
    messages: list[AnyMessage] = [
        HumanMessage(content=image if with_image else "no image"),
        HumanMessage(content="explain it"),
    ]

    forwarded = await _forwarded(
        BackendRunMiddleware(MagicMock(), None),
        messages,
        _ANTHROPIC,
        state={"messages": messages, "open_swe_call": call.model_dump()},
    )

    assert forwarded.model is (_OPENAI if with_image else _FIREWORKS)
