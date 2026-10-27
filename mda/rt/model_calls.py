"""What every model call here sends the provider, how long it may take, and why it failed.

A thread's history is replayed to the provider on every call, and to whichever
provider routing picks, so a block one provider wrote can break every later call
on another. A provider stall that never raises would park the run with nothing
for the retry to react to. Anthropic's prompt cache is marked here too, where the
call's real model is known.
"""

import hashlib
import logging
from collections.abc import Awaitable, Callable, Sequence
from typing import Final

import httpx
import httpx2
from deepagents.middleware.unsupported_content import UnsupportedContentMiddleware
from langchain.agents.middleware import (
    AgentMiddleware,
)
from langchain.agents.middleware.types import ExtendedModelResponse, ModelRequest, ModelResponse
from langchain_anthropic import ChatAnthropic
from langchain_anthropic.middleware import AnthropicPromptCachingMiddleware
from langchain_core.exceptions import (
    ContextOverflowError,
    ModelAPIError,
    ModelConnectionError,
    ModelError,
    ModelNotFoundError,
    ModelRateLimitError,
    ModelTimeoutError,
)
from langchain_core.messages import AIMessage, AnyMessage, ToolMessage
from langchain_fireworks import ChatFireworks
from langchain_fireworks.middleware import FireworksPromptCachingMiddleware
from langchain_openai import ChatOpenAI
from langgraph.config import get_config
from runtime_api.loop import ModelFailureCode

from rt.compaction import PreparedConversation, SummarizationStateMiddleware, context_overflow
from rt.model_budget import ModelCallAttempt
from rt.model_budget import ModelCallTimeoutError as ModelCallTimeoutError
from rt.tracing import MIDDLEWARE_TRACE_POLICY

logger = logging.getLogger(__name__)

MODEL_CALL_TIMEOUT_SECONDS: Final = 900.0
# As the in-process graphs': a tool call can outlast the 5m default between model calls.
PROMPT_CACHE_TTL: Final = "1h"
# Bindings nest a few deep at most; the bound stops a self-referencing chain.
_MAX_BINDINGS: Final = 10
_SUBAGENT_TIMED_OUT: Final = "This subagent could not finish: its model call timed out."


_FAILURE_CODES_BY_TYPE: Final[dict[str, ModelFailureCode]] = {
    "RateLimitError": "provider_rate_limited",
    "OverloadedError": "provider_overloaded",
    "APIConnectionError": "provider_unavailable",
    "APIError": "provider_unavailable",
    "APIStatusError": "provider_unavailable",
    "InternalServerError": "provider_unavailable",
    "APITimeoutError": "provider_timeout",
    "ModelCallTimeoutError": "provider_timeout",
}


# LangChain's provider-neutral model errors, which integrations raise beside their SDK's own.
_FAILURE_CODES_BY_CLASS: Final[tuple[tuple[type[ModelError], ModelFailureCode], ...]] = (
    (ModelRateLimitError, "provider_rate_limited"),
    (ModelTimeoutError, "provider_timeout"),
    (ModelConnectionError, "provider_unavailable"),
    (ContextOverflowError, "context_too_long"),
    (ModelNotFoundError, "model_unavailable"),
    (ModelAPIError, "provider_unavailable"),
)


def http_status(exc: BaseException) -> int | None:
    """The HTTP status a failed call reports: on the error, on its response, or in an int ``code``.

    httpx keeps it on the response; google-genai's own errors keep it in ``code``.
    """
    response = getattr(exc, "response", None)
    for status in (
        getattr(exc, "status_code", None),
        getattr(response, "status_code", None),
        getattr(exc, "code", None),
    ):
        if isinstance(status, int):
            return status
    return None


def provider_failure_code(exc: BaseException) -> ModelFailureCode | None:
    """Why the provider failed a call, by its status and error body; None if not a provider failure.

    Provider-agnostic on purpose: a gateway can surface an overload as anything
    from a 529 to a bare ``APIError`` carrying ``overloaded_error`` in its body.
    """
    status = http_status(exc)
    body = getattr(exc, "body", None)
    provider_code = ""
    if isinstance(body, dict):
        inner = body.get("error")
        if isinstance(inner, dict):
            provider_code = str(inner.get("type") or inner.get("code") or "")
    haystack = f"{provider_code} {exc}".lower()
    if status == 429 or "rate_limit" in haystack:
        return "provider_rate_limited"
    if status == 529 or "overloaded" in haystack:
        return "provider_overloaded"
    if "context_length_exceeded" in haystack or "prompt is too long" in haystack:
        return "context_too_long"
    if "model_not_available" in haystack or "model_not_found" in haystack:
        return "model_unavailable"
    if isinstance(status, int) and status >= 500:
        return "provider_unavailable"
    for model_error, failure in _FAILURE_CODES_BY_CLASS:
        if isinstance(exc, model_error):
            return failure
    # A client or gateway can let the HTTP library's own failure through unwrapped.
    if isinstance(exc, (httpx.TimeoutException, httpx2.TimeoutException)):
        return "provider_timeout"
    if isinstance(exc, (httpx.TransportError, httpx2.TransportError)):
        return "provider_unavailable"
    return _FAILURE_CODES_BY_TYPE.get(type(exc).__name__)


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
    trace_policy = MIDDLEWARE_TRACE_POLICY


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

    def __init__(self, timeout_seconds: float = MODEL_CALL_TIMEOUT_SECONDS) -> None:
        self._timeout_seconds = timeout_seconds

    @property
    def name(self) -> str:
        return "UnsupportedContentMiddleware"

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse | ExtendedModelResponse:
        attempt = ModelCallAttempt(request, self._timeout_seconds)
        conversation = PreparedConversation(request, attempt)

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

        async def fireworks(request: ModelRequest) -> ModelResponse:
            cache = FireworksPromptCachingMiddleware(unsupported_model_behavior="ignore")
            response = await cache.awrap_model_call(request, affinity)
            if not isinstance(response, ModelResponse):
                raise TypeError("Fireworks caching returned a non-model response")
            return response

        async def replay(request: ModelRequest) -> ModelResponse:
            return await ReplayableHistoryMiddleware().awrap_model_call(request, fireworks)

        async def filtered(request: ModelRequest) -> ModelResponse:
            return await UnsupportedContentMiddleware().awrap_model_call(request, replay)

        async def selected(request: ModelRequest) -> ModelResponse:
            prepared = await conversation.prepare(request)
            try:
                return await conversation.generate(prepared, filtered)
            except Exception as exc:
                if attempt.emitted or not context_overflow(exc):
                    raise
                prepared = await conversation.prepare(request, rejected=exc)
                return await conversation.generate(prepared, filtered)

        response = await selected(request)
        return conversation.finish(response)


class _PromptCacheSlot(_ModelCallGuard):
    def __init__(self, provider: str) -> None:
        self._provider = provider

    @property
    def name(self) -> str:
        return f"{self._provider}PromptCachingMiddleware"


class SubagentTimeoutReplyMiddleware(_ModelCallGuard):
    """Answer with a subagent's timed-out model call instead of raising it.

    A subagent's ``task`` call does not catch what it raises, so a stall would end
    the run that delegated to it. As its answer, the
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
    timeout_seconds: float = MODEL_CALL_TIMEOUT_SECONDS,
    *,
    subagent: bool = False,
    recovery: AgentMiddleware | None = None,
) -> list[AgentMiddleware]:
    """Prepare once, then recover each provider request without changing models."""
    return [
        SummarizationStateMiddleware(),
        *([SubagentTimeoutReplyMiddleware()] if subagent else []),
        *([recovery] if recovery is not None else []),
        _PromptCacheSlot("Anthropic"),
        _PromptCacheSlot("Fireworks"),
        FinalModelCallMiddleware(timeout_seconds),
    ]
