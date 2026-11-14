import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from copy import deepcopy
from datetime import timedelta
from typing import Protocol, cast
from unittest.mock import AsyncMock, MagicMock

import httpx
import httpx2
import openai
import pytest
from deepagents.middleware._utils import append_to_system_message
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
from rt.gateway import RUN_GATEWAY
from rt.middleware import BackendRunMiddleware, ModelCall
from rt.model_calls import (
    MODEL_OUTAGE_MESSAGE,
    ModelCallTimeoutError,
    ModelCallTimeoutMiddleware,
    ReplayableHistoryMiddleware,
    call_with_fallback,
    model_call_guards,
)
from rt.model_retries import ModelRetriesMiddleware
from rt.models import ModelSpec, build_model
from rt.run_prompt import RunPromptMiddleware, ThreadPromptMiddleware
from runtime_api.catalog import Catalog, LoopRules, RoutingRules, RunModels
from runtime_api.loop import GatewayCredential
from runtime_api.model_errors import is_model_failure_reply

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
    assert call.model is not None
    catalog = Catalog(
        loop=LoopRules(routing=RoutingRules(mode="auto")),
        models=RunModels(
            model=call.model,
            subagent_model=call.model,
            use_gateway=False,
            routes={"fast": call.model},
        ),
    )
    run = Run(RunContext.model_validate({"__catalog": catalog.model_dump(mode="json")}))
    run.route = "fast"

    async def handler(request: ModelRequest) -> ModelResponse:
        assert request.model is (_OPENAI if with_image else _FIREWORKS)
        assert await RUN_GATEWAY.route() == ("default" if with_image else "auto")
        return ModelResponse(result=[AIMessage(content="ok")])

    await BackendRunMiddleware(run.backend).awrap_model_call(
        ModelRequest(
            model=_ANTHROPIC,
            messages=messages,
            state=cast(AgentState, {"messages": messages, "open_swe_call": call.model_dump()}),
        ),
        handler,
    )


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
    """The run's shared prompt and the workspace's each end in an Anthropic cache
    breakpoint ahead of the skills and the thread's own part, keeping a request within
    four, the tools take none, and a fallback to another provider carries none."""
    built: dict[str, BaseChatModel] = {"anthropic:opus": _ANTHROPIC, "openai:gpt": _OPENAI}
    monkeypatch.setattr(
        "rt.middleware.build_model", lambda spec, *, use_gateway: built[spec.model_id]
    )
    monkeypatch.setenv("OPEN_SWE_BACKEND_URL", "https://backend.invalid")
    primary = ModelSpec(model_id="anthropic:opus", kwargs={})
    catalog = Catalog(
        loop=LoopRules(routing=RoutingRules(mode="auto")),
        models=RunModels(
            model=primary,
            subagent_model=primary,
            use_gateway=False,
            routes={"fast": primary},
        ),
    )
    run = Run(RunContext.model_validate({"__catalog": catalog.model_dump(mode="json")}))
    run.route = "fast"
    static = SystemMessage(content="The graph's own instructions")
    sent: list[ModelRequest] = []

    async def handler(request: ModelRequest) -> ModelResponse:
        sent.append(request)
        assert await RUN_GATEWAY.route() == ("auto" if request.model is _ANTHROPIC else "default")
        if request.model is _ANTHROPIC:
            raise TimeoutError("overloaded")
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
            workspace_prompt="The workspace's prompt",
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
        {"type": "text", "text": "The graph's own instructions"},
        {"type": "text", "text": "Every thread's prompt", "cache_control": cached},
        {"type": "text", "text": "The workspace's prompt", "cache_control": cached},
        {"type": "text", "text": "\n\nSkills"},
        {"type": "text", "text": "First thread's part", "cache_control": cached},
    ]
    assert first.model_settings == {"cache_control": cached}
    assert first.tools == [_search]
    second_blocks = cast(SystemMessage, second.system_message).content
    assert second_blocks[:4] == cast(SystemMessage, first.system_message).content[:4]
    assert second_blocks[4] == {
        "type": "text",
        "text": "Next thread's part",
        "cache_control": cached,
    }
    assert cast(SystemMessage, first_fallback.system_message).content == [
        {"type": "text", "text": "The graph's own instructions"},
        {"type": "text", "text": "Every thread's prompt"},
        {"type": "text", "text": "The workspace's prompt"},
        {"type": "text", "text": "\n\nSkills"},
        {"type": "text", "text": "First thread's part"},
    ]
    assert first_fallback.model_settings == {}
    assert static.content == "The graph's own instructions"


def _status_error(status: int, body: dict[str, object] | None = None) -> openai.APIStatusError:
    request = httpx2.Request("POST", "https://api.openai.com/v1/responses")
    response = httpx2.Response(status, request=request, json=body or {})
    return openai.APIStatusError("provider error", response=response, body=body)


class _Attempts:
    """A provider that fails each attempt with the next error, then answers."""

    def __init__(self, *errors: BaseException) -> None:
        self.errors = list(errors)
        self.models: list[object] = []

    async def __call__(self, request: ModelRequest) -> ModelResponse:
        self.models.append(request.model)
        if self.errors:
            raise self.errors.pop(0)
        return ModelResponse(result=[AIMessage(content="answered")])


def _fallback_call(
    attempts: _Attempts, schedule: tuple[float, ...] = (0.0, 0.0, 0.0)
) -> Awaitable[ModelResponse]:
    return call_with_fallback(
        ModelRequest(model=_ANTHROPIC, messages=[]),
        attempts,
        lambda: _OPENAI,
        backoff_schedule=schedule,
    )


@pytest.mark.parametrize(
    "error",
    [
        TimeoutError("stalled"),
        ModelCallTimeoutError("deadline"),
        _status_error(503),
        _status_error(529),
        httpx2.RemoteProtocolError("peer closed connection (incomplete chunked read)"),
        # A streamed overload carries no status, only its message.
        openai.APIError(
            "Our servers are currently overloaded. Please try again later.",
            request=httpx2.Request("POST", "https://api.openai.com/v1/responses"),
            body=None,
        ),
    ],
)
async def test_a_transient_failure_goes_to_the_fallback(error: BaseException) -> None:
    attempts = _Attempts(error)

    response = await _fallback_call(attempts)

    assert response.result[0].content == "answered"
    assert attempts.models == [_ANTHROPIC, _OPENAI]


async def test_attempts_alternate_between_the_model_and_its_fallback() -> None:
    attempts = _Attempts(_status_error(503), _status_error(502))

    await _fallback_call(attempts)

    assert attempts.models == [_ANTHROPIC, _OPENAI, _ANTHROPIC]


async def test_attempts_wait_out_the_backoff_with_jitter(monkeypatch: pytest.MonkeyPatch) -> None:
    sleep = AsyncMock()
    monkeypatch.setattr(
        "rt.model_calls.asyncio",
        MagicMock(
            sleep=sleep,
            get_running_loop=asyncio.get_running_loop,
            timeout_at=asyncio.timeout_at,
        ),
    )
    monkeypatch.setattr("rt.model_calls.random.uniform", lambda low, high: high)
    attempts = _Attempts(TimeoutError(), TimeoutError())

    await _fallback_call(attempts, schedule=(0.0, 8.0))

    # The failover is immediate; the next attempt waits its delay and up to a quarter more.
    sleep.assert_awaited_once_with(10.0)


@pytest.mark.parametrize("error", [ValueError("not transient"), _status_error(400)])
async def test_an_error_another_attempt_would_repeat_raises_at_once(error: BaseException) -> None:
    attempts = _Attempts(error)

    with pytest.raises(type(error)):
        await _fallback_call(attempts)
    assert attempts.models == [_ANTHROPIC]


async def test_a_lasting_outage_ends_with_a_message_instead_of_failing_the_run() -> None:
    attempts = _Attempts(*[_status_error(503) for _ in range(4)])

    response = await _fallback_call(attempts, schedule=(0.0, 0.0, 0.0))

    assert response.result[0].content == MODEL_OUTAGE_MESSAGE
    assert is_model_failure_reply(response.result[0])
    assert attempts.models == [_ANTHROPIC, _OPENAI, _ANTHROPIC, _OPENAI]


async def test_a_model_the_workspace_cannot_use_says_so() -> None:
    body: dict[str, object] = {
        "error": {"code": "model_not_found", "message": "The model does not exist."}
    }
    request = httpx2.Request("POST", "https://api.openai.com/v1/responses")
    response = httpx2.Response(404, request=request, json=body)
    attempts = _Attempts(openai.NotFoundError("not found", response=response, body=body))

    answer = await _fallback_call(attempts)

    content = answer.result[0].content
    assert isinstance(content, str) and "OpenAI returned: The model does not exist." in content
    assert attempts.models == [_ANTHROPIC]
    assert is_model_failure_reply(answer.result[0])


async def test_a_run_whose_login_lapsed_tells_the_person_instead_of_calling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Its request's own auth would fail as a connection error the loop retries for the
    whole budget; the run answers why at once instead."""
    monkeypatch.setattr(
        RUN_GATEWAY,
        "credential",
        AsyncMock(return_value=GatewayCredential(unavailable="Sign in to Robin again.")),
    )
    attempts = _Attempts()

    for fallback in (None, lambda: _OPENAI):
        answer = await call_with_fallback(
            ModelRequest(model=_ANTHROPIC, messages=[]), attempts, fallback
        )
        assert answer.result[0].content == "Sign in to Robin again."
        assert is_model_failure_reply(answer.result[0])
    assert attempts.models == []

    await call_with_fallback(
        ModelRequest(model=_ANTHROPIC, messages=[]), attempts, None, use_gateway=False
    )
    assert attempts.models == [_ANTHROPIC]


async def test_without_a_fallback_the_call_is_made_once() -> None:
    attempts = _Attempts(TimeoutError("stalled"))

    with pytest.raises(TimeoutError):
        await call_with_fallback(ModelRequest(model=_ANTHROPIC, messages=[]), attempts, None)
    assert attempts.models == [_ANTHROPIC]


async def test_a_fallback_that_cannot_be_built_leaves_the_retries_to_the_model() -> None:
    attempts = _Attempts(TimeoutError("stalled"), TimeoutError("stalled"))

    def unbuildable() -> BaseChatModel:
        raise ValueError("BASETEN_API_KEY is required when Gateway routing is disabled")

    response = await call_with_fallback(
        ModelRequest(model=_ANTHROPIC, messages=[]),
        attempts,
        unbuildable,
        backoff_schedule=(0.0, 0.0, 0.0),
    )

    assert response.result[0].content == "answered"
    assert attempts.models == [_ANTHROPIC, _ANTHROPIC, _ANTHROPIC]


async def test_the_retries_end_within_the_budget() -> None:
    """However the attempts stall, the call answers before the budget is out, so the
    backend's stalled-run sweep (twice ``MODEL_CALL_BUDGET``) never takes a live run."""

    async def stalled(request: ModelRequest) -> ModelResponse:
        await asyncio.sleep(60)
        raise AssertionError("cut off by the budget")

    clock = asyncio.get_running_loop()
    started = clock.time()
    response = await call_with_fallback(
        ModelRequest(model=_ANTHROPIC, messages=[]),
        stalled,
        lambda: _OPENAI,
        backoff_schedule=(0.0, 0.0),
        budget=timedelta(seconds=0.05),
    )

    assert response.result[0].content == MODEL_OUTAGE_MESSAGE
    assert clock.time() - started < 1


async def test_a_wait_that_would_outlast_the_budget_is_not_waited() -> None:
    attempts = _Attempts(TimeoutError("stalled"))

    response = await call_with_fallback(
        ModelRequest(model=_ANTHROPIC, messages=[]),
        attempts,
        lambda: _OPENAI,
        backoff_schedule=(30.0,),
        budget=timedelta(seconds=5),
    )

    assert response.result[0].content == MODEL_OUTAGE_MESSAGE
    assert attempts.models == [_ANTHROPIC]


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


@pytest.mark.parametrize("outcome", ["error", "exhausted", "stall", "continues"])
async def test_streamed_partial_output_is_superseded_on_retry_or_fallback(
    monkeypatch: pytest.MonkeyPatch,
    outcome: str,
) -> None:
    from langchain_core.callbacks import AsyncCallbackManagerForLLMRun
    from langchain_core.messages import AIMessageChunk
    from langchain_core.outputs import ChatGenerationChunk

    attempts = 0
    monkeypatch.setattr("rt.model_retries.random.uniform", lambda *args: 0.0)

    async def partial(
        self: ChatOpenAI,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: object,
    ) -> AsyncIterator[ChatGenerationChunk]:
        nonlocal attempts
        attempts += 1
        chunk = ChatGenerationChunk(
            message=AIMessageChunk(
                content="recovered"
                if attempts > (6 if outcome == "exhausted" else 1)
                else "incomplete",
                id=f"attempt-{attempts}",
            )
        )
        if run_manager is not None:
            await run_manager.on_llm_new_token("incomplete", chunk=chunk)
        yield chunk
        if attempts > (6 if outcome == "exhausted" else 1):
            return
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
    backend_middleware = BackendRunMiddleware(run.backend)
    monkeypatch.setattr(backend_middleware, "_before_agent", AsyncMock(return_value=(None, False)))
    for name in ("_before_model", "_after_model", "_after_agent"):
        monkeypatch.setattr(backend_middleware, name, AsyncMock(return_value=None))
    graph = create_agent(
        model=model,
        middleware=cast(
            list[AgentMiddleware],
            [
                backend_middleware,
                *model_call_guards(timeout_seconds=2.0 if outcome == "exhausted" else 0.15),
            ],
        ),
    )
    call = ModelCall(
        system_prompt="prompt",
        fallback=ModelSpec(model_id="openai:backup", kwargs={"api_key": "test"}),
    )
    stream = graph.astream(
        {"messages": [HumanMessage(content="go")], "open_swe_call": call.model_dump()},
        stream_mode="messages",
    )
    chunks = cast(list[tuple[BaseMessage, dict[str, object]]], [chunk async for chunk in stream])
    # A stalled stream times out its attempt, and the fallback answers.
    expected = 7 if outcome == "exhausted" else 1 if outcome == "continues" else 2
    assert attempts == expected
    superseded = {
        message.id for message, _ in chunks if message.response_metadata.get("open_swe_superseded")
    }
    assert superseded == {f"attempt-{index}" for index in range(1, expected)}
    if expected > 1:
        assert any(
            message.id == f"attempt-{expected}" and "recovered" in str(message.content)
            for message, _ in chunks
        )
    assert f"attempt-{expected}" not in superseded


@pytest.mark.parametrize("jitter", ["lower", "upper"])
async def test_retry_backoff_allows_a_temporary_provider_outage_to_recover(
    monkeypatch: pytest.MonkeyPatch, jitter: str
) -> None:
    elapsed = 0.0
    attempts = 0
    events: list[dict[str, object]] = []

    async def sleep(delay: float) -> None:
        nonlocal elapsed
        elapsed += delay

    async def provider(request: ModelRequest) -> ModelResponse:
        nonlocal attempts
        attempts += 1
        if elapsed < 20:
            raise httpx.ReadError("provider temporarily unavailable")
        return ModelResponse(result=[AIMessage(content="recovered")])

    monkeypatch.setattr("rt.model_retries.asyncio.sleep", sleep)
    monkeypatch.setattr(
        "rt.model_retries.random.uniform",
        lambda lower, upper: lower if jitter == "lower" else upper,
    )
    monkeypatch.setattr("rt.model_retries.get_stream_writer", lambda: events.append)
    request = ModelRequest(
        model=ChatOpenAI(model="test", api_key="test", max_retries=0),
        messages=[HumanMessage(content="go")],
    )

    response = await ModelRetriesMiddleware().awrap_model_call(request, provider)

    assert response.result[0].content == "recovered"
    assert attempts <= 6
    assert 20 <= elapsed <= 60
    assert len(events) == attempts - 1


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
