import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from copy import deepcopy
from functools import partial
from typing import Protocol, cast
from unittest.mock import AsyncMock

import httpx2
import openai
import pytest
from deepagents import create_deep_agent
from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import (
    AgentState,
    ExtendedModelResponse,
    ModelRequest,
    ModelResponse,
)
from langchain_anthropic import ChatAnthropic
from langchain_core.callbacks import AsyncCallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel, LanguageModelInput
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    AnyMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langchain_core.runnables.config import RunnableConfig
from langchain_fireworks import ChatFireworks
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import SecretStr
from rt.backend import Run, RunContext
from rt.middleware import BackendRunMiddleware, ModelCall
from rt.model_budget import ModelCallAttempt, provider_attempts
from rt.model_calls import (
    ModelCallTimeoutError,
    OpenAIPromptCachingMiddleware,
    ReplayableHistoryMiddleware,
    model_call_guards,
)
from rt.models import ModelSpec, build_model
from rt.provider_recovery import Backoff, ProviderRecovery
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

    request = ModelRequest(model=_ANTHROPIC, messages=[])
    with pytest.raises(ModelCallTimeoutError) as raised:
        await ModelCallAttempt(request, timeout_seconds=0.01).call(lambda: hang(request))
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


class _RuntimeModel(FakeListChatModel):
    failures: int = 0
    attempts: int = 0
    summaries: int = 0
    summary_failures: int = 0
    received: list[list[BaseMessage]] = []

    def bind_tools(self, *args: object, **kwargs: object) -> BaseChatModel:
        return self

    async def ainvoke(
        self,
        input: LanguageModelInput,
        config: RunnableConfig | None = None,
        *,
        stop: list[str] | None = None,
        **kwargs: object,
    ) -> AIMessage:
        if config and config.get("metadata", {}).get("lc_source") == "summarization":
            self.summaries += 1
            if self.summaries <= self.summary_failures:
                raise httpx2.ReadTimeout("summary provider unavailable")
            return AIMessage("summary")
        return await super().ainvoke(input, config, stop=stop, **kwargs)

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: object,
    ) -> ChatResult:
        self.attempts += 1
        self.received.append(messages)
        if self.attempts <= self.failures:
            raise httpx2.ReadTimeout("provider unavailable")
        return ChatResult(generations=[ChatGeneration(message=AIMessage("done"))])


class _RuntimeRoute(AgentMiddleware):
    def __init__(self, model: BaseChatModel, recovery: ProviderRecovery | None = None) -> None:
        self.model = model
        self.recovery = recovery
        self.preparations = 0
        self.reports = 0

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        self.preparations += 1
        if self.recovery is None:
            result = await handler(request.override(model=self.model))
        else:
            with provider_attempts(partial(self.recovery.call, model=None, via_gateway=False)):
                result = await handler(request.override(model=self.model))
        self.reports += 1
        return result


async def test_final_routed_compaction_survives_provider_retries_and_checkpoint_resume(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPEN_SWE_BACKEND_URL", "https://backend.invalid")
    backend = Run(RunContext()).backend
    recovery = ProviderRecovery(backend, Backoff(initial_seconds=0))
    monkeypatch.setattr(recovery, "_tell", AsyncMock())
    model = _RuntimeModel(
        responses=["unused"], failures=1, summary_failures=1, profile={"max_input_tokens": 5000}
    )
    route = _RuntimeRoute(model, recovery)
    graph = create_deep_agent(
        _RuntimeModel(responses=["unused"], profile={"max_input_tokens": 1000000}),
        checkpointer=InMemorySaver(),
        middleware=[route, *model_call_guards()],
    )
    config: RunnableConfig = {"configurable": {"thread_id": "routed"}}
    messages = [HumanMessage("history " * 300, id=f"h-{i}") for i in range(20)]
    first = await graph.ainvoke({"messages": messages}, config)
    assert model.summaries == 2
    assert model.attempts == 2
    assert sum(data["content"].count("## Summarized at") for data in first["files"].values()) == 1
    checkpoint = await graph.aget_state(config)
    assert checkpoint.values["_summarization_event"]["cutoff_index"] > 0
    assert len(model.received[-1]) < len(messages)
    resumed = await graph.ainvoke({"messages": [HumanMessage("next")]}, config)
    assert model.summaries == 2
    assert sum(data["content"].count("## Summarized at") for data in resumed["files"].values()) == 1
    assert route.preparations == route.reports == 2


async def test_openai_affinity_is_scoped_to_actual_provider_and_preserves_overrides() -> None:
    middleware = OpenAIPromptCachingMiddleware()
    captured: dict[str, list[ModelRequest]] = {}

    async def capture(request: ModelRequest) -> ModelResponse:
        captured.setdefault(request.messages[-1].text, []).append(request)
        return ModelResponse(result=[AIMessage("done")])

    class Affinity(AgentMiddleware):
        async def awrap_model_call(
            self, request: ModelRequest, handler: Callable[[ModelRequest], Awaitable[ModelResponse]]
        ) -> ModelResponse:
            return await middleware.awrap_model_call(request.override(model=_OPENAI), capture)

    graph = create_deep_agent(_RuntimeModel(responses=["unused"]), middleware=[Affinity()])
    await asyncio.gather(
        *[
            graph.ainvoke(
                {"messages": [HumanMessage(thread)]}, {"configurable": {"thread_id": thread}}
            )
            for thread in ["α", "β", "α"]
        ]
    )
    alpha = [r.model_settings["prompt_cache_key"] for r in captured["α"]]
    beta = captured["β"][0].model_settings["prompt_cache_key"]
    assert alpha[0] == alpha[1] != beta
    assert all(len(key) == 64 for key in [*alpha, beta])
    assert _OPENAI.model_kwargs.get("prompt_cache_key") is None
    forwarded = await _forwarded(middleware, [HumanMessage("go")], _ANTHROPIC)
    assert "prompt_cache_key" not in forwarded.model_settings
    explicit = _OPENAI.model_copy(update={"extra_body": {"prompt_cache_key": "explicit"}})
    forwarded = await _forwarded(middleware, [HumanMessage("go")], explicit)
    assert "prompt_cache_key" not in forwarded.model_settings


class _InterruptedStream(_RuntimeModel):
    async def _astream(self, *args: object, **kwargs: object) -> AsyncIterator[ChatGenerationChunk]:
        yield ChatGenerationChunk(message=AIMessageChunk(content="partial"))
        raise httpx2.ReadTimeout("interrupted stream")


async def test_partial_stream_is_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    primary = _InterruptedStream(responses=["unused"])
    monkeypatch.setenv("OPEN_SWE_BACKEND_URL", "https://backend.invalid")
    recovery = ProviderRecovery(Run(RunContext()).backend, Backoff(initial_seconds=0))
    told = AsyncMock()
    monkeypatch.setattr(recovery, "_tell", told)
    route = _RuntimeRoute(primary, recovery)
    graph = create_deep_agent(primary, middleware=[route, *model_call_guards()])
    emitted: list[str] = []
    with pytest.raises(httpx2.ReadTimeout, match="interrupted stream"):
        async for chunk, _ in graph.astream(
            {"messages": [HumanMessage("go")]}, stream_mode="messages"
        ):
            assert isinstance(chunk, AIMessageChunk)
            emitted.append(chunk.text)
    assert emitted == ["partial"]
    told.assert_not_awaited()


@pytest.mark.parametrize(
    "model_id,key",
    [
        ("anthropic:claude-test", "ANTHROPIC_API_KEY"),
        ("openai:gpt-test", "OPENAI_API_KEY"),
        ("fireworks:accounts/fireworks/models/test", "FIREWORKS_API_KEY"),
        ("google_genai:gemini-test", "GOOGLE_API_KEY"),
    ],
)
async def test_provider_retry_override_cannot_multiply_attempts(
    monkeypatch: pytest.MonkeyPatch, model_id: str, key: str
) -> None:
    monkeypatch.setenv(key, "test")
    model = build_model(ModelSpec(model_id=model_id, kwargs={"max_retries": 6}), use_gateway=False)
    assert model.model_dump()["max_retries"] == 0


async def test_each_provider_attempt_has_its_own_deadline_and_cancellation_propagates() -> None:
    request = ModelRequest(model=_ANTHROPIC, messages=[])

    async def hang() -> ModelResponse:
        await asyncio.sleep(60)
        raise AssertionError("deadline did not cancel")

    with pytest.raises(ModelCallTimeoutError):
        await ModelCallAttempt(request, 0.01).call(hang)
    cancelled = AsyncMock(side_effect=asyncio.CancelledError)
    with pytest.raises(asyncio.CancelledError):
        await ModelCallAttempt(request).call(cancelled)
    assert cancelled.await_count == 1


async def test_final_prompt_and_tool_schema_alone_trigger_compaction() -> None:
    class FinalShape(AgentMiddleware):
        async def awrap_model_call(
            self, request: ModelRequest, handler: Callable[[ModelRequest], Awaitable[ModelResponse]]
        ) -> ModelResponse:
            return await handler(
                request.override(
                    system_message=SystemMessage("context " * 1200),
                    tools=[
                        {
                            "type": "function",
                            "function": {
                                "name": "large",
                                "description": "schema " * 1200,
                                "parameters": {"type": "object", "properties": {}},
                            },
                        }
                    ],
                )
            )

    model = _RuntimeModel(responses=["unused"], profile={"max_input_tokens": 8000})
    route = _RuntimeRoute(model)
    graph = create_deep_agent(model, middleware=[route, FinalShape(), *model_call_guards()])
    await graph.ainvoke({"messages": [HumanMessage("history " * 60) for _ in range(20)]})
    assert model.summaries == 1
    assert len(model.received[-1]) < 21
    assert model.received[-1][0].text == "context " * 1200


async def test_provider_context_rejection_retains_one_compaction_recovery() -> None:
    class RejectContext(_RuntimeModel):
        async def _agenerate(
            self,
            messages: list[BaseMessage],
            stop: list[str] | None = None,
            run_manager: AsyncCallbackManagerForLLMRun | None = None,
            **kwargs: object,
        ) -> ChatResult:
            if self.attempts == 0:
                self.attempts += 1
                response = httpx2.Response(
                    400, request=httpx2.Request("POST", "https://provider.invalid")
                )
                raise openai.APIStatusError(
                    "maximum context length exceeded", response=response, body=None
                )
            return await super()._agenerate(messages, stop, run_manager, **kwargs)

    model = RejectContext(responses=["unused"], profile={"max_input_tokens": 20000})
    graph = create_deep_agent(model, middleware=[*model_call_guards()])
    result = await graph.ainvoke({"messages": [HumanMessage("history " * 100) for _ in range(20)]})
    assert result["messages"][-1].content == "done"
    assert model.summaries == 1
    assert model.attempts == 2


async def test_sync_task_subagent_compacts_with_its_selected_model() -> None:
    from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
    from langchain_core.messages import ToolCall
    from langchain_core.tools import tool

    @tool
    async def history() -> str:
        """Read historical context."""
        return "historical context " * 600

    class Child(_RuntimeModel):
        async def _agenerate(
            self,
            messages: list[BaseMessage],
            stop: list[str] | None = None,
            run_manager: AsyncCallbackManagerForLLMRun | None = None,
            **kwargs: object,
        ) -> ChatResult:
            self.attempts += 1
            if self.attempts <= 8:
                reply = AIMessage(
                    "", tool_calls=[ToolCall(name="history", args={}, id=f"read-{self.attempts}")]
                )
            else:
                reply = AIMessage("child done")
            return ChatResult(generations=[ChatGeneration(message=reply)])

    class Parent(GenericFakeChatModel):
        def bind_tools(self, *args: object, **kwargs: object) -> BaseChatModel:
            return self

    child = Child(responses=["unused"], profile={"max_input_tokens": 12000})
    parent = Parent(
        messages=iter(
            [
                AIMessage(
                    "",
                    tool_calls=[
                        ToolCall(
                            name="task",
                            args={
                                "subagent_type": "general-purpose",
                                "description": "Read the history",
                            },
                            id="delegate",
                        )
                    ],
                ),
                AIMessage("parent done"),
            ]
        )
    )
    graph = create_deep_agent(
        parent,
        subagents=[
            {
                "name": "general-purpose",
                "description": "Investigate",
                "system_prompt": "Investigate",
                "model": child,
                "tools": [history],
                "middleware": [_RuntimeRoute(child), *model_call_guards(subagent=True)],
            }
        ],
        middleware=[*model_call_guards()],
    )
    result = await graph.ainvoke({"messages": [HumanMessage("delegate")]})
    assert result["messages"][-1].content == "parent done"
    assert child.summaries >= 1


async def test_recovery_deadlines_restart_per_attempt_and_give_up_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPEN_SWE_BACKEND_URL", "https://backend.invalid")
    recovery = ProviderRecovery(Run(RunContext()).backend, Backoff(initial_seconds=0))
    notices = AsyncMock()
    monkeypatch.setattr(recovery, "_tell", notices)
    monkeypatch.setattr("rt.provider_recovery.run_id", lambda: "run")
    request = ModelRequest(model=_ANTHROPIC, messages=[])
    calls = 0

    async def generate() -> ModelResponse:
        nonlocal calls
        calls += 1
        if calls == 1:
            await asyncio.sleep(60)
        await asyncio.sleep(0.005)
        return ModelResponse(result=[AIMessage("recovered")])

    with provider_attempts(partial(recovery.call, model=None, via_gateway=False)):
        result = await ModelCallAttempt(request, 0.02).call(generate)
    assert result.result[0].text == "recovered"
    assert calls == 2
    assert [call.args[0].waiting for call in notices.await_args_list] == [True, False]
    expired = ProviderRecovery(recovery.backend, Backoff(give_up_after_seconds=0))
    error = httpx2.ReadTimeout("unavailable")
    handler = AsyncMock(side_effect=error)
    with provider_attempts(partial(expired.call, model=None, via_gateway=False)):
        with pytest.raises(httpx2.ReadTimeout) as raised:
            await ModelCallAttempt(request).call(handler)
    assert raised.value is error
    assert handler.await_count == 1


async def test_concurrent_summary_recovery_cancellation_keeps_run_notices_balanced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPEN_SWE_BACKEND_URL", "https://backend.invalid")
    recovery = ProviderRecovery(Run(RunContext()).backend, Backoff(initial_seconds=0))
    notices = AsyncMock()
    monkeypatch.setattr(recovery, "_tell", notices)
    monkeypatch.setattr("rt.provider_recovery.run_id", lambda: "run")
    request = ModelRequest(model=_ANTHROPIC, messages=[])
    entered = [asyncio.Event(), asyncio.Event()]
    release = asyncio.Event()
    counts = [0, 0]

    async def summary(index: int) -> ModelResponse:
        counts[index] += 1
        if counts[index] == 1:
            raise httpx2.ReadTimeout("summary unavailable")
        entered[index].set()
        await release.wait()
        return ModelResponse(result=[AIMessage("summary")])

    with provider_attempts(partial(recovery.call, model=None, via_gateway=False)):
        first = asyncio.create_task(
            ModelCallAttempt(request).call(lambda: summary(0), streaming=False)
        )
        second = asyncio.create_task(
            ModelCallAttempt(request).call(lambda: summary(1), streaming=False)
        )
        await asyncio.wait_for(asyncio.gather(*(event.wait() for event in entered)), timeout=1)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        assert [call.args[0].waiting for call in notices.await_args_list] == [True]
        release.set()
        await second
    assert [call.args[0].waiting for call in notices.await_args_list] == [True, False]
