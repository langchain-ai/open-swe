"""What every model call here sends the provider, and how long it may take.

A thread's history is replayed to the provider on every call, and to whichever
provider routing or the fallback picks, so a block one provider wrote can break
every later call on another. A provider stall that never raises would park the
run with nothing for the retry or the fallback to react to. Anthropic's prompt
cache is marked here too, where the call's real model is known.
"""

import asyncio
import hashlib
import logging
from collections.abc import Awaitable, Callable, Sequence
from typing import Final

from deepagents.middleware.unsupported_content import UnsupportedContentMiddleware
from langchain.agents.middleware import (
    AgentMiddleware,
    TracePolicy,
    omit_payload,
)
from langchain.agents.middleware.types import ExtendedModelResponse, ModelRequest, ModelResponse
from langchain_anthropic import ChatAnthropic
from langchain_anthropic.middleware import AnthropicPromptCachingMiddleware
from langchain_core.messages import AIMessage, AnyMessage, ToolMessage
from langchain_fireworks import ChatFireworks
from langchain_openai import ChatOpenAI
from langgraph.config import get_config

from rt.compaction import PreparedConversation, SummarizationStateMiddleware
from rt.model_budget import ModelCallBudget, fallback_model, retry_delay

logger = logging.getLogger(__name__)

MODEL_CALL_TIMEOUT_SECONDS: Final = 900.0
# As the in-process graphs': a tool call can outlast the 5m default between model calls.
PROMPT_CACHE_TTL: Final = "1h"
# Bindings nest a few deep at most; the bound stops a self-referencing chain.
_MAX_BINDINGS: Final = 10
_SUBAGENT_TIMED_OUT: Final = "This subagent could not finish: its model call timed out."


class ModelCallTimeoutError(TimeoutError):
    """A model call exceeded its wall-clock deadline.

    The backend's run-complete webhook reads this class name as a provider timeout.
    """


def _chat_model(model: object) -> object:
    """The chat model under any bindings around it."""
    for _ in range(_MAX_BINDINGS):
        bound = getattr(model, "bound", None)
        if bound is None or bound is model:
            break
        model = bound
    return model


def _replayable(block: object, model: object) -> bool:
    if not isinstance(block, dict):
        return True
    if isinstance(model, ChatAnthropic):
        # Anthropic rejects an empty thinking block.
        return not (block.get("type") == "thinking" and not block.get("thinking"))
    if isinstance(model, ChatOpenAI) and model.store is False:
        # Without stored responses, OpenAI cannot resolve a reasoning item by id alone.
        item_id = block.get("id")
        return not (
            block.get("type") == "reasoning"
            and isinstance(item_id, str)
            and item_id.startswith("rs_")
            and not block.get("encrypted_content")
        )
    return True


def _for_provider(message: AnyMessage, model: object) -> AnyMessage:
    if not isinstance(message, AIMessage):
        return message
    update: dict[str, object] = {}
    if isinstance(message.content, list):
        content = [block for block in message.content if _replayable(block, model)]
        if len(content) != len(message.content):
            update["content"] = content
    if isinstance(model, ChatFireworks) and "function_call" in message.additional_kwargs:
        # The gateway rejects the legacy field, which ChatFireworks forwards even
        # beside `tool_calls`.
        update["additional_kwargs"] = {
            k: v for k, v in message.additional_kwargs.items() if k != "function_call"
        }
    return message.model_copy(update=update) if update else message


def _in_call_order(messages: Sequence[AnyMessage]) -> list[AnyMessage]:
    """Each parallel batch's results in the order of its tool calls.

    They land in completion order, and the provider's prompt cache is a byte-prefix
    match: one swapped pair re-prefills everything after it on a later run.
    """
    ordered: list[AnyMessage] = []
    index = 0
    while index < len(messages):
        message = messages[index]
        ordered.append(message)
        index += 1
        if not isinstance(message, AIMessage) or len(message.tool_calls) < 2:
            continue
        positions: dict[str, int] = {}
        for position, call in enumerate(message.tool_calls):
            if call["id"]:
                positions.setdefault(call["id"], position)
        start = index
        while index < len(messages) and isinstance(messages[index], ToolMessage):
            index += 1
        ordered.extend(
            sorted(
                messages[start:index],
                key=lambda result: positions.get(
                    getattr(result, "tool_call_id", ""), len(positions)
                ),
            )
        )
    return ordered


class _ModelCallGuard(AgentMiddleware):
    # Its request carries the whole conversation, which the model call's own trace has.
    trace_policy = TracePolicy(process_inputs=omit_payload)


class ReplayableHistoryMiddleware(_ModelCallGuard):
    """Send each call a history the provider it goes to accepts, in a stable order."""

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        model = _chat_model(request.model)
        messages = [_for_provider(message, model) for message in _in_call_order(request.messages)]
        if any(a is not b for a, b in zip(messages, request.messages, strict=True)):
            request = request.override(messages=messages)
        return await handler(request)


class ModelCallTimeoutMiddleware(_ModelCallGuard):
    """Bound preparation, summary requests, retries and fallback by one deadline."""

    def __init__(self, timeout_seconds: float = MODEL_CALL_TIMEOUT_SECONDS) -> None:
        self._timeout_seconds = timeout_seconds

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        deadline = asyncio.timeout(self._timeout_seconds)
        try:
            async with deadline:
                return await handler(request)
        except TimeoutError as exc:
            if not deadline.expired():
                raise
            raise ModelCallTimeoutError(
                f"Model call exceeded the {self._timeout_seconds}s deadline"
            ) from exc


class OpenAIPromptCachingMiddleware(_ModelCallGuard):
    """Give OpenAI a stable opaque per-thread cache key unless explicitly configured."""

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        model = _chat_model(request.model)
        if isinstance(model, ChatOpenAI):
            sources = [request.model_settings, model.model_kwargs, model.extra_body or {}]
            extra = request.model_settings.get("extra_body")
            if isinstance(extra, dict):
                sources.append(extra)
            if not any("prompt_cache_key" in source for source in sources):
                thread = get_config().get("configurable", {}).get("thread_id")
                if isinstance(thread, str) and thread:
                    key = hashlib.sha256(thread.encode()).hexdigest()
                    request = request.override(
                        model_settings={**request.model_settings, "prompt_cache_key": key}
                    )
        return await handler(request)


class FinalModelCallMiddleware(_ModelCallGuard):
    """Use the harness's final slot for routed compaction and provider-only retries."""

    @property
    def name(self) -> str:
        return "UnsupportedContentMiddleware"

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse | ExtendedModelResponse:
        budget = ModelCallBudget()
        conversation = PreparedConversation(request, budget)

        async def cached(request: ModelRequest) -> ModelResponse:
            cache = AnthropicPromptCachingMiddleware(
                ttl=PROMPT_CACHE_TTL, unsupported_model_behavior="ignore"
            )
            response = await cache.awrap_model_call(request, handler)
            if not isinstance(response, ModelResponse):
                raise TypeError("Provider caching returned a non-model response")
            return response

        async def affinity(request: ModelRequest) -> ModelResponse:
            return await OpenAIPromptCachingMiddleware().awrap_model_call(request, cached)

        async def replay(request: ModelRequest) -> ModelResponse:
            return await ReplayableHistoryMiddleware().awrap_model_call(request, affinity)

        async def filtered(request: ModelRequest) -> ModelResponse:
            return await UnsupportedContentMiddleware().awrap_model_call(request, replay)

        async def selected(request: ModelRequest) -> ModelResponse:
            prepared = await conversation.prepare(request)
            return await conversation.generate(prepared, filtered)

        try:
            response = await selected(request)
        except Exception as exc:
            if exc is not conversation.provider_failure or not budget.can_retry(exc):
                raise
            fallback = fallback_model()
            if fallback is None:
                raise
            delay = retry_delay(exc, 1)
            logger.warning(
                "Switching to the fallback model", extra={"delay_seconds": delay}, exc_info=True
            )
            await asyncio.sleep(delay)
            response = await selected(request.override(model=fallback))
        return conversation.finish(response)


class _PromptCacheSlot(_ModelCallGuard):
    @property
    def name(self) -> str:
        return "AnthropicPromptCachingMiddleware"


class SubagentTimeoutReplyMiddleware(_ModelCallGuard):
    """Answer with a subagent's timed-out model call instead of raising it.

    A subagent has no fallback model, and its ``task`` call does not catch what it
    raises, so a stall would end the run that delegated to it. As its answer, the
    timeout goes back to that agent, which can try again or carry on.
    """

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        try:
            return await handler(request)
        except TimeoutError as exc:
            logger.warning("A subagent's model call timed out", exc_info=True)
            return ModelResponse(
                result=[AIMessage(content=f"{_SUBAGENT_TIMED_OUT} ({type(exc).__name__}: {exc})")]
            )


def model_call_guards(
    timeout_seconds: float = MODEL_CALL_TIMEOUT_SECONDS, *, subagent: bool = False
) -> list[AgentMiddleware]:
    """Budget the whole call; compact and retry only after the final request is shaped."""
    return [
        SummarizationStateMiddleware(),
        *([SubagentTimeoutReplyMiddleware()] if subagent else []),
        ModelCallTimeoutMiddleware(timeout_seconds),
        _PromptCacheSlot(),
        FinalModelCallMiddleware(),
    ]
