import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from copy import deepcopy
from typing import Protocol, cast
from unittest.mock import AsyncMock, MagicMock

import httpx
import httpx2
import openai
import pytest
from langchain.agents import create_agent
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
from rt.model_retries import ModelRetriesMiddleware, PartialModelOutputError
from rt.models import ModelSpec, build_model
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
    assert isinstance(raised.value, TimeoutError)


async def test_a_provider_timeout_is_not_reported_as_budget_exhaustion() -> None:
    error = TimeoutError("provider timed out before the overall deadline")
    handler = AsyncMock(side_effect=error)

    with pytest.raises(TimeoutError) as raised:
        await ModelCallTimeoutMiddleware().awrap_model_call(
            cast(ModelRequest, MagicMock()), handler
        )

    assert raised.value is error


@pytest.mark.parametrize(
    "primary_stalls,fallback_stalls", [(False, True), (True, True), (True, False)]
)
async def test_primary_and_fallback_share_one_elapsed_budget(
    monkeypatch: pytest.MonkeyPatch, primary_stalls: bool, fallback_stalls: bool
) -> None:
    monkeypatch.setenv("OPEN_SWE_BACKEND_URL", "https://backend.invalid")
    monkeypatch.setattr("rt.middleware.build_model", lambda spec, *, use_gateway: _OPENAI)
    run = Run(RunContext.model_validate({"__catalog": Catalog().model_dump(mode="json")}))
    call = ModelCall(
        system_prompt="prompt",
        fallback=ModelSpec(model_id="openai:fallback", kwargs={}),
        use_gateway=False,
    )
    messages: list[AnyMessage] = [HumanMessage(content="go")]
    request = ModelRequest(
        model=_ANTHROPIC,
        messages=messages,
        state=cast(AgentState, {"messages": messages, "open_swe_call": call.model_dump()}),
    )
    attempted: list[BaseChatModel | str] = []
    cancelled: list[BaseChatModel | str] = []

    async def handler(request: ModelRequest) -> ModelResponse:
        attempted.append(request.model)
        try:
            if request.model is _ANTHROPIC:
                await asyncio.sleep(60 if primary_stalls else 0.03)
                raise TimeoutError("provider timeout")
            await asyncio.sleep(60 if fallback_stalls else 0)
            return ModelResponse(result=[AIMessage(content="too late")])
        except asyncio.CancelledError:
            cancelled.append(request.model)
            raise

    middleware = BackendRunMiddleware(run.backend, model_timeout_seconds=0.05)
    if fallback_stalls:
        with pytest.raises(ModelCallTimeoutError):
            await middleware.awrap_model_call(request, handler)
    else:
        response = await middleware.awrap_model_call(request, handler)
        assert isinstance(response, ModelResponse)
        assert response.result[0].content == "too late"

    assert attempted == [_ANTHROPIC, _OPENAI]
    assert cancelled == ([_ANTHROPIC] if primary_stalls else []) + (
        [_OPENAI] if fallback_stalls else []
    )


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


async def test_the_graphs_system_prompt_leads_each_runs_and_is_cached_on_its_own(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every run's prompt follows the graph's own, which ends in an Anthropic cache
    breakpoint that a fallback to another provider does not carry."""
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

    for prompt in ("First run's prompt", "Next run's prompt"):
        call = ModelCall(
            system_prompt=prompt,
            model=ModelSpec(model_id="anthropic:opus", kwargs={}),
            fallback=ModelSpec(model_id="openai:gpt", kwargs={}),
            use_gateway=False,
        )
        messages: list[AnyMessage] = [HumanMessage(content="go")]
        request = ModelRequest(
            model=_ANTHROPIC,
            messages=messages,
            system_message=static,
            state=cast(AgentState, {"messages": messages, "open_swe_call": call.model_dump()}),
        )
        await BackendRunMiddleware(run.backend).awrap_model_call(request, handler)

    cached = {"type": "ephemeral", "ttl": "1h"}
    first, first_fallback, second, _ = (cast(SystemMessage, r.system_message) for r in sent)
    assert first.content == [
        {"type": "text", "text": "The graph's own instructions", "cache_control": cached},
        {"type": "text", "text": "First run's prompt"},
    ]
    assert second.content[0] == first.content[0]
    assert second.content[1] == {"type": "text", "text": "Next run's prompt"}
    assert first_fallback.content == [
        {"type": "text", "text": "The graph's own instructions"},
        {"type": "text", "text": "First run's prompt"},
    ]
    assert static.content == "The graph's own instructions"


@pytest.mark.parametrize("status,expected_attempts", [(429, 3), (401, 1), (503, 6)])
async def test_provider_http_retries_are_owned_by_model_middleware(
    status: int, expected_attempts: int
) -> None:
    attempts = 0

    def respond(request: httpx2.Request) -> httpx2.Response:
        nonlocal attempts
        attempts += 1
        if status == 429 and attempts == 1:
            return httpx2.Response(
                200,
                json={
                    "id": "chat-tool",
                    "object": "chat.completion",
                    "created": 1,
                    "model": "test",
                    "choices": [
                        {
                            "index": 0,
                            "finish_reason": "tool_calls",
                            "message": {
                                "role": "assistant",
                                "content": None,
                                "tool_calls": [
                                    {
                                        "id": "completed",
                                        "type": "function",
                                        "function": {"name": "complete", "arguments": "{}"},
                                    }
                                ],
                            },
                        }
                    ],
                },
            )
        if status == 429 and attempts == 3:
            return httpx2.Response(
                200,
                json={
                    "id": "chat-test",
                    "object": "chat.completion",
                    "created": 1,
                    "model": "test",
                    "choices": [
                        {
                            "index": 0,
                            "finish_reason": "stop",
                            "message": {"role": "assistant", "content": "recovered"},
                        }
                    ],
                },
            )
        return httpx2.Response(
            status,
            headers={"retry-after": "0"},
            json={"error": {"message": "provider error", "type": "test", "code": None}},
        )

    completed: list[str] = []

    @tool
    async def complete() -> str:
        """Complete one side effect."""
        completed.append("done")
        return "done"

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as client:
        model = build_model(
            ModelSpec(
                model_id="openai:test",
                kwargs={
                    "api_key": "test",
                    "max_retries": 9,
                },
            ),
            use_gateway=False,
        )
        assert isinstance(model, ChatOpenAI)
        assert model.max_retries is not None
        model = model.model_copy(
            update={
                "use_responses_api": False,
                "include": None,
                "async_client": openai.AsyncOpenAI(
                    api_key="test", max_retries=model.max_retries, http_client=client
                ).chat.completions,
            }
        )
        graph = create_agent(model=model, tools=[complete], middleware=model_call_guards())
        if status == 429:
            result = await graph.ainvoke({"messages": [HumanMessage(content="go")]})
            assert result["messages"][-1].content == "recovered"
            assert completed == ["done"]
        else:
            with pytest.raises(openai.APIStatusError):
                await graph.ainvoke({"messages": [HumanMessage(content="go")]})
    assert attempts == expected_attempts


@pytest.mark.parametrize(
    "retry_after,budget,expected_attempts", [("61", 60.0, 1), ("0.03", 0.05, 2)]
)
async def test_retry_after_beyond_sleep_budget_is_not_retried_early(
    monkeypatch: pytest.MonkeyPatch, retry_after: str, budget: float, expected_attempts: int
) -> None:
    monkeypatch.setattr("rt.model_retries.MAX_RETRY_SLEEP_SECONDS", budget)
    attempts = 0

    def respond(request: httpx2.Request) -> httpx2.Response:
        nonlocal attempts
        attempts += 1
        return httpx2.Response(
            429,
            headers={"retry-after": retry_after},
            json={"error": {"message": "wait", "type": "rate_limit", "code": None}},
        )

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as client:
        sdk = openai.AsyncOpenAI(api_key="test", max_retries=0, http_client=client)
        model = ChatOpenAI(
            model="test", api_key="test", max_retries=0, async_client=sdk.chat.completions
        )
        graph = create_agent(model=model, middleware=model_call_guards())
        with pytest.raises(openai.RateLimitError):
            await graph.ainvoke({"messages": [HumanMessage(content="go")]})
    assert attempts == expected_attempts


@pytest.mark.parametrize("outcome", ["error", "stall", "continues"])
async def test_streamed_partial_output_is_not_retried_or_sent_to_fallback(
    monkeypatch: pytest.MonkeyPatch,
    outcome: str,
) -> None:
    from langchain_core.callbacks import AsyncCallbackManagerForLLMRun
    from langchain_core.messages import AIMessageChunk
    from langchain_core.outputs import ChatGenerationChunk

    attempts = 0

    async def partial(
        self: ChatOpenAI,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: object,
    ) -> AsyncIterator[ChatGenerationChunk]:
        nonlocal attempts
        attempts += 1
        chunk = ChatGenerationChunk(message=AIMessageChunk(content="incomplete"))
        if run_manager is not None:
            await run_manager.on_llm_new_token("incomplete", chunk=chunk)
        yield chunk
        if outcome == "continues":
            await asyncio.sleep(0.12)
            return
        if outcome == "stall":
            await asyncio.sleep(60)
        raise httpx.ReadError("stream dropped")

    monkeypatch.setattr(ChatOpenAI, "_astream", partial)
    monkeypatch.setenv("OPEN_SWE_BACKEND_URL", "https://backend.invalid")
    run = Run(RunContext.model_validate({"__catalog": Catalog().model_dump(mode="json")}))
    model = ChatOpenAI(model="test", api_key="test", max_retries=0)
    backend_middleware = BackendRunMiddleware(run.backend, model_timeout_seconds=0.15)
    for name in ("_before_agent", "_before_model", "_after_model", "_after_agent"):
        monkeypatch.setattr(backend_middleware, name, AsyncMock(return_value=None))
    graph = create_agent(
        model=model,
        middleware=cast(list[AgentMiddleware], [backend_middleware, *model_call_guards()]),
    )
    call = ModelCall(
        system_prompt="prompt",
        fallback=ModelSpec(model_id="openai:backup", kwargs={"api_key": "test"}),
    )
    stream = graph.astream(
        {"messages": [HumanMessage(content="go")], "open_swe_call": call.model_dump()},
        stream_mode="messages",
    )
    if outcome == "continues":
        chunks = [chunk async for chunk in stream]
        assert chunks
    else:
        expected = ModelCallTimeoutError if outcome == "stall" else PartialModelOutputError
        with pytest.raises(expected):
            async for _ in stream:
                pass
    assert attempts == 1


async def test_cancelling_retry_backoff_does_not_start_another_model_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backing_off = asyncio.Event()
    monkeypatch.setattr("rt.model_retries.random.uniform", lambda *args: 10.0)
    monkeypatch.setattr(
        "rt.model_retries.get_stream_writer", lambda: lambda event: backing_off.set()
    )
    request = ModelRequest(
        model=ChatOpenAI(model="test", api_key="test", max_retries=0),
        messages=[HumanMessage(content="go")],
    )
    handler = AsyncMock(side_effect=httpx.ReadError("connection dropped"))
    task = asyncio.create_task(ModelRetriesMiddleware().awrap_model_call(request, handler))
    await asyncio.wait_for(backing_off.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert handler.await_count == 1
