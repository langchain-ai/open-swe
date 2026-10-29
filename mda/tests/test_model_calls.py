import asyncio
from collections.abc import Awaitable, Callable
from copy import deepcopy
from typing import Protocol, cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from deepagents.middleware._utils import append_to_system_message
from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import (
    AgentState,
    ExtendedModelResponse,
    ModelRequest,
    ModelResponse,
)
from langchain_anthropic import ChatAnthropic
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    AnyMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.tools import tool
from langchain_fireworks import ChatFireworks
from langchain_openai import ChatOpenAI
from pydantic import SecretStr
from rt.backend import Run, RunContext
from rt.middleware import BackendRunMiddleware, ModelCall
from rt.model_calls import (
    ModelCallTimeoutError,
    ModelCallTimeoutMiddleware,
    ReplayableHistoryMiddleware,
    model_call_guards,
)
from rt.models import ModelSpec
from rt.run_prompt import RunPromptMiddleware, ThreadPromptMiddleware
from runtime_api.catalog import Catalog

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
    ) -> ModelResponse | ExtendedModelResponse: ...


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

    monkeypatch.setenv("OPEN_SWE_BACKEND_URL", "https://backend.invalid")
    # A run whose catalog names no models of its own, so the call's own decide.
    run = Run(RunContext.model_validate({"__catalog": Catalog().model_dump(mode="json")}))
    forwarded = await _forwarded(
        BackendRunMiddleware(run.backend),
        messages,
        _ANTHROPIC,
        state={"messages": messages, "open_swe_call": call.model_dump()},
    )

    assert forwarded.model is (_OPENAI if with_image else _FIREWORKS)


class _Skills(AgentMiddleware):
    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        system = append_to_system_message(request.system_message, "Skills")
        return await handler(request.override(system_message=system))


def _through(
    layers: list[AgentMiddleware], handler: Callable[[ModelRequest], Awaitable[ModelResponse]]
) -> Callable[[ModelRequest], Awaitable[ModelResponse]]:
    async def call(request: ModelRequest) -> ModelResponse:
        response = await layers[0].awrap_model_call(request, _through(layers[1:], handler))
        assert isinstance(response, ModelResponse)
        return response

    return call if layers else handler


@tool
def _search(query: str) -> str:
    """Search."""
    return query


async def test_the_system_prompt_is_cached_up_to_what_only_the_thread_says(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The graph's prompt and the run's shared one each end in an Anthropic cache
    breakpoint ahead of the skills and the thread's own part, the tools take none, and a
    fallback to another provider carries none."""
    built: dict[str, BaseChatModel] = {"anthropic:opus": _ANTHROPIC, "openai:gpt": _OPENAI}
    monkeypatch.setattr(
        "rt.middleware.build_model", lambda spec, *, use_gateway: built[spec.model_id]
    )
    monkeypatch.setenv("OPEN_SWE_BACKEND_URL", "https://backend.invalid")
    run = Run(RunContext.model_validate({"__catalog": Catalog().model_dump(mode="json")}))
    static = SystemMessage(content="The graph's own instructions")
    sent: list[ModelRequest] = []

    async def handler(request: ModelRequest) -> ModelResponse:
        sent.append(request)
        if request.model is _ANTHROPIC:
            raise RuntimeError("overloaded")
        return ModelResponse(result=[AIMessage(content="ok")])

    layers: list[AgentMiddleware] = [
        RunPromptMiddleware(),
        _Skills(),
        ThreadPromptMiddleware(),
        model_call_guards()[-1],
    ]
    for thread in ("First thread's part", "Next thread's part"):
        call = ModelCall(
            system_prompt="Every thread's prompt",
            thread_prompt=thread,
            model=ModelSpec(model_id="anthropic:opus", kwargs={}),
            fallback=ModelSpec(model_id="openai:gpt", kwargs={}),
            use_gateway=False,
        )
        messages: list[AnyMessage] = [HumanMessage(content="go")]
        request = ModelRequest(
            model=_ANTHROPIC,
            messages=messages,
            system_message=static,
            tools=[_search],
            state=cast(AgentState, {"messages": messages, "open_swe_call": call.model_dump()}),
        )
        await BackendRunMiddleware(run.backend).awrap_model_call(request, _through(layers, handler))

    cached = {"type": "ephemeral", "ttl": "1h"}
    first, first_fallback, second, _ = sent
    assert cast(SystemMessage, first.system_message).content == [
        {"type": "text", "text": "The graph's own instructions", "cache_control": cached},
        {"type": "text", "text": "Every thread's prompt", "cache_control": cached},
        {"type": "text", "text": "\n\nSkills"},
        {"type": "text", "text": "First thread's part", "cache_control": cached},
    ]
    assert first.model_settings == {"cache_control": cached}
    assert first.tools == [_search]
    second_blocks = cast(SystemMessage, second.system_message).content
    assert second_blocks[:3] == cast(SystemMessage, first.system_message).content[:3]
    assert second_blocks[3] == {
        "type": "text",
        "text": "Next thread's part",
        "cache_control": cached,
    }
    assert cast(SystemMessage, first_fallback.system_message).content == [
        {"type": "text", "text": "The graph's own instructions"},
        {"type": "text", "text": "Every thread's prompt"},
        {"type": "text", "text": "\n\nSkills"},
        {"type": "text", "text": "First thread's part"},
    ]
    assert first_fallback.model_settings == {}
    assert static.content == "The graph's own instructions"
