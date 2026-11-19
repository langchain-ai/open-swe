"""What every model call here sends the provider, how long it may take, and what
happens when the provider fails it.

A thread's history is replayed to the provider on every call, and to whichever
provider routing or the fallback picks, so a block one provider wrote can break
every later call on another. A provider stall that never raises would park the
run with nothing for the retry or the fallback to react to. Anthropic's prompt
cache is marked here too, where the call's real model is known.

A call the provider fails transiently goes again, alternating between the call's
model and its fallback with a growing wait between attempts. Both providers can be
routed through the same LLM Gateway, so a gateway outage takes out the
"cross-provider" fallback too, and a single immediate failover cannot ride out even
a short shared outage (the gateway's 502 page says "try again in 30 seconds"). Once
every attempt failed, the run ends with a message saying so instead of a crash: its
progress is checkpointed, so the person can start it again.

A provider can also refuse a reply or cut it off at its output limit, both as an
answer rather than an error. A refusal goes once to the other provider, then ends
the run with a reply saying so that keeps what the model wrote; a reply cut off at
the output limit is logged, and a subagent's says so to the agent that delegated.
"""

import asyncio
import logging
import random
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Any, Final

import anthropic
import httpx2
import openai
from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelCallResult, ModelRequest, ModelResponse
from langchain_anthropic import ChatAnthropic
from langchain_anthropic.middleware import AnthropicPromptCachingMiddleware
from langchain_core.exceptions import ModelError, ModelRefusalError
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, AnyMessage, ToolMessage
from langchain_fireworks import ChatFireworks
from langchain_openai import ChatOpenAI
from runtime_api.model_errors import MODEL_FAILURE_REPLY_KEY, classify_exception, exception_fields
from runtime_api.models import MODEL_CALL_BUDGET
from runtime_api.provenance import Provenance

from rt.gateway import RUN_GATEWAY
from rt.model_retries import ModelOutput, ModelRetriesMiddleware
from rt.tracing import MIDDLEWARE_TRACE_POLICY, TurnTrace

logger = logging.getLogger(__name__)

# One attempt on one model, its own retries and reasoning included.
MODEL_CALL_TIMEOUT_SECONDS: Final = 900.0
# As the in-process graphs': a tool call can outlast the 5m default between model calls.
PROMPT_CACHE_TTL: Final = "1h"
# Bindings nest a few deep at most; the bound stops a self-referencing chain.
_MAX_BINDINGS: Final = 10
_SUBAGENT_TIMED_OUT: Final = "This subagent could not finish: its model call timed out."
_SUBAGENT_CUT_OFF: Final = (
    "This subagent's reply was cut off at the model's output limit ({stop_reason})"
)
# A reply the provider stopped at its output limit, by where each provider says so:
# Anthropic's ``stop_reason``, Chat Completions' (and Fireworks') ``finish_reason``.
# The Responses API marks it ``incomplete`` instead.
_OUTPUT_LIMIT_STOPS: Final = {"stop_reason": "max_tokens", "finish_reason": "length"}

# Seconds waited before each attempt after the first; one more attempt than delays.
# The first failover is immediate: one provider's outage should not delay the other.
# Later waits grow past the ~30s the gateway's 502 page asks for. Each attempt also
# gets the model's own retries (``rt.model_retries``), and all of them
# together end within ``MODEL_CALL_BUDGET``, well before the backend takes the run
# for stalled.
BACKOFF_SCHEDULE: Sequence[float] = (0.0, 5.0, 15.0, 30.0, 45.0)
MODEL_OUTAGE_MESSAGE: Final = (
    "I wasn't able to reach the language model providers after several retries "
    "(both the primary and fallback models returned transient errors, e.g. "
    "502/503/overloaded). This is a temporary provider or gateway outage, not a "
    "problem with your task. My progress so far has been saved — please retrigger "
    "the run in a few minutes to continue."
)
MODEL_REFUSAL_MESSAGE: Final = (
    "The model provider blocked {reply}. Asking the same thing again will likely be "
    "blocked too: rephrase the request or pick a different model, then retry."
)
RETRYABLE_STATUS_CODES: Final = frozenset({408, 409, 425, 429, 500, 502, 503, 504, 529})
_TRANSIENT_EXCEPTIONS: Final[tuple[type[BaseException], ...]] = (
    anthropic.APIConnectionError,
    anthropic.APITimeoutError,
    anthropic.RateLimitError,
    anthropic.InternalServerError,
    openai.APIConnectionError,
    openai.APITimeoutError,
    openai.RateLimitError,
    openai.InternalServerError,
    httpx2.TransportError,
    # A model call's deadline too: a wedged call is where the other provider helps most.
    TimeoutError,
)


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


def _is_legacy_httpx_transport_error(exc: BaseException) -> bool:
    return exc.__class__.__module__.partition(".")[0] == "httpx" and any(
        cls.__name__ == "TransportError" and cls.__module__.partition(".")[0] == "httpx"
        for cls in exc.__class__.__mro__
    )


def is_transient(exc: BaseException) -> bool:
    """Whether another attempt, on this provider or the other, may succeed."""
    if isinstance(exc, _TRANSIENT_EXCEPTIONS) or _is_legacy_httpx_transport_error(exc):
        return True
    if isinstance(exc, ModelError):
        return exc.is_retryable
    # Catches OverloadedError (529) and other 5xx/429 surfaced as APIStatusError.
    if isinstance(exc, (anthropic.APIStatusError, openai.APIStatusError)):
        status = getattr(exc, "status_code", None)
        if isinstance(status, int) and status in RETRYABLE_STATUS_CODES:
            return True
    if type(exc) is openai.APIError:
        return classify_exception(exc) == "provider_overloaded"
    return False


def _error_body(exc: BaseException) -> dict[str, Any]:
    body = getattr(exc, "body", None)
    return body if isinstance(body, dict) else {}


def _nested_str(data: dict[str, Any], *keys: str) -> str | None:
    current: Any = data
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current if isinstance(current, str) and current else None


def model_access_message(exc: BaseException) -> str | None:
    """What to tell the person when the workspace may not use the call's model."""
    if isinstance(exc, (anthropic.APIStatusError, openai.APIStatusError)):
        provider_message = _nested_str(_error_body(exc), "error", "message") or str(exc)
        if exc.status_code == 402 and "blocked by gateway policies" in provider_message.lower():
            return (
                "The model gateway blocked this request because a spending limit was reached. "
                "Ask a LangSmith organization admin to review the gateway's cost policies "
                "and usage before retrying. Sending the same request again will not "
                "resolve the limit."
            )
    if isinstance(exc, anthropic.BadRequestError):
        body = _error_body(exc)
        if _nested_str(body, "error", "details", "error_code") == "model_not_available":
            provider_message = _nested_str(body, "error", "message") or str(exc)
            return (
                "The selected Anthropic model is not available to this workspace. "
                f"Anthropic returned: {provider_message} "
                "Choose a different model or update the workspace's Anthropic access and retry."
            )
    if isinstance(exc, (openai.BadRequestError, openai.NotFoundError)):
        body = _error_body(exc)
        if _nested_str(body, "error", "code") in {"model_not_found", "model_not_available"}:
            provider_message = _nested_str(body, "error", "message") or str(exc)
            return (
                "The selected OpenAI model is not available to this workspace. "
                f"OpenAI returned: {provider_message} "
                "Choose a different model or update the workspace's OpenAI access and retry."
            )
    return None


def _ignore(_on_fallback: bool) -> None:
    return None


async def call_with_fallback(
    request: ModelRequest,
    handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    fallback: Callable[[], BaseChatModel] | None,
    *,
    attempting: Callable[[bool], None] = _ignore,
    backoff_schedule: Sequence[float] | None = None,
    budget: timedelta = MODEL_CALL_BUDGET,
) -> ModelResponse:
    """``request``, and again while it fails transiently, alternating with ``fallback``.

    Without a fallback the call is made once. ``attempting`` hears before each attempt
    after the first whether it goes to the fallback; a fallback that cannot be built
    leaves the retries to the call's own model. A provider that refuses the workspace
    the model answers with why; any other error that is not transient raises at once.
    A refused reply goes once to the other model, then answers with what the model wrote
    and that the provider blocked the rest. Once the attempts or ``budget`` run out, the
    outage message answers instead.
    """
    if (unavailable := await RUN_GATEWAY.unavailable()) is not None:
        return _failure_reply(unavailable, "model_access")
    schedule = (
        tuple(BACKOFF_SCHEDULE if backoff_schedule is None else backoff_schedule)
        if fallback is not None
        else ()
    )
    clock = asyncio.get_running_loop()
    deadline = clock.time() + budget.total_seconds()
    fallback_request: ModelRequest | None = None
    fallback_unbuilt = True
    last: BaseException | None = None
    made = 0
    for attempt in range(len(schedule) + 1):
        output = ModelOutput()
        delay = 0.0
        if last is not None:
            delay = schedule[attempt - 1]
            delay += random.uniform(0, delay * 0.25) if delay > 0 else 0.0
            if clock.time() + delay >= deadline:
                break
        try:
            if attempt % 2 == 1 and fallback_unbuilt and fallback is not None:
                fallback_unbuilt = False
                try:
                    fallback_request = request.override(model=fallback())
                except Exception:
                    logger.exception("Could not build the fallback model")
            if fallback_request is None and isinstance(last, ModelRefusalError):
                break
            on_fallback = attempt % 2 == 1 and fallback_request is not None
            attempt_request = fallback_request if on_fallback and fallback_request else request
            if last is not None:
                attempting(on_fallback)
                if delay > 0:
                    await asyncio.sleep(delay)
            made += 1
            with output.tracking():
                async with asyncio.timeout_at(deadline):
                    return await handler(attempt_request)
        except Exception as exc:
            if (message := model_access_message(exc)) is not None:
                logger.warning(
                    "Model access error surfaced to the person",
                    extra={"model_access_error": exception_fields(exc)},
                )
                return _failure_reply(message, "model_access")
            if isinstance(exc, ModelRefusalError):
                logger.warning(
                    "Model refused the reply",
                    extra={"model_refusal": {"attempt": made, **exception_fields(exc)}},
                )
                refused = isinstance(last, ModelRefusalError)
                last = exc
                # The reply that answers instead keeps what the model wrote.
                output.supersede()
                if fallback is None or refused:
                    break
                continue
            if fallback is None or not is_transient(exc):
                raise
            # The next attempt answers instead: what this one streamed is taken back.
            output.supersede()
            logger.warning(
                "Model call failed transiently",
                extra={"model_call_retry": {"attempt": made, **exception_fields(exc)}},
            )
            if isinstance(last, ModelRefusalError):
                break
            last = exc
    assert last is not None  # every attempt failed, or the budget ran out after one
    if isinstance(last, ModelRefusalError):
        written = last.raw_message.text.strip() if last.raw_message is not None else ""
        if not written:
            return _failure_reply(MODEL_REFUSAL_MESSAGE.format(reply="this reply"), "refusal")
        blocked = MODEL_REFUSAL_MESSAGE.format(reply="the rest of this reply")
        return _failure_reply(f"{written}\n\n{blocked}", "refusal")
    logger.error(
        "Model call failed after retries across primary and fallback",
        exc_info=last,
        extra={"model_call_failure": {"attempts": made, **exception_fields(last)}},
    )
    return _failure_reply(MODEL_OUTAGE_MESSAGE, "outage")


def _failure_reply(text: str, kind: str) -> ModelResponse:
    """The answer for a call that could not be made, marked so the loop posts it as it is.

    It has its id before it reaches state, as a model's own reply does, for the transcript.
    """
    reply = AIMessage(
        content=text, id=str(uuid.uuid4()), response_metadata={MODEL_FAILURE_REPLY_KEY: kind}
    )
    return ModelResponse(result=[reply])


class _ModelCallGuard(AgentMiddleware):
    # Its request carries the whole conversation, which the model call's own trace has.
    trace_policy = MIDDLEWARE_TRACE_POLICY


class ReplayableHistoryMiddleware(_ModelCallGuard):
    """Send each call a history the provider it goes to accepts, in a stable order."""

    @staticmethod
    def _context_order(messages: Sequence[AnyMessage]) -> list[AnyMessage]:
        """Keep personal context last within each uninterrupted group of context messages."""
        ordered: list[AnyMessage] = []
        personal: list[AnyMessage] = []
        for message in messages:
            provenance = Provenance.of(message)
            if provenance is None or provenance.kind != "context":
                ordered.extend(personal)
                personal.clear()
            elif provenance.sender.startswith("user:") or provenance.sender == "run:recent-threads":
                personal.append(message)
                continue
            ordered.append(message)
        ordered.extend(personal)
        return ordered

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        model = _chat_model(request.model)
        messages = [
            _for_provider(message, model)
            for message in _in_call_order(self._context_order(request.messages))
        ]
        if any(a is not b for a, b in zip(messages, request.messages, strict=True)):
            request = request.override(messages=messages)
        return await handler(request)


class ModelCallTimeoutMiddleware(_ModelCallGuard):
    """Fail a model call that outlives its deadline instead of letting it hang."""

    def __init__(self, timeout_seconds: float = MODEL_CALL_TIMEOUT_SECONDS) -> None:
        super().__init__()
        self._timeout_seconds = timeout_seconds

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        async with self.deadline():
            return await handler(request)

    @asynccontextmanager
    async def deadline(self) -> AsyncIterator[asyncio.Timeout]:
        """Bound model attempts together without reclassifying provider timeouts."""
        timeout = asyncio.timeout(self._timeout_seconds)
        try:
            async with timeout:
                yield timeout
        except TimeoutError as exc:
            if not timeout.expired():
                raise
            logger.warning(
                "Model call exceeded its deadline", extra={"timeout_seconds": self._timeout_seconds}
            )
            raise ModelCallTimeoutError(
                f"Model call exceeded the {self._timeout_seconds}s deadline"
            ) from exc


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


class OutputLimitMiddleware(_ModelCallGuard):
    """Say when the provider cut a reply off at its output limit.

    The provider answers such a reply as a success. A subagent's reply without tool
    calls is its final answer, and the agent that delegated to it would take it, or its
    silence, as finished; it says it was cut off instead, so that agent can ask again
    in smaller steps. The agent's own reply is left as it is, logged and tagged on the
    turn's trace.
    """

    def __init__(self, *, subagent: bool = False) -> None:
        super().__init__()
        self._subagent = subagent

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        response = await handler(request)
        reply = response.result[-1] if response.result else None
        if not isinstance(reply, AIMessage) or (stop := self.output_limit_stop(reply)) is None:
            return response
        logger.warning(
            "Model reply was cut off at its output limit",
            extra={
                "model_output_limit": {
                    "stop_reason": stop,
                    "subagent": self._subagent,
                    "tool_calls": len(reply.tool_calls),
                    "invalid_tool_calls": len(reply.invalid_tool_calls),
                    "text_chars": len(reply.text),
                }
            },
        )
        if not self._subagent:
            TurnTrace.record_output_limit(stop)
            return response
        if reply.tool_calls:
            return response
        notice = _SUBAGENT_CUT_OFF.format(stop_reason=stop)
        written = reply.text.strip()
        content = f"{written}\n\n[{notice}.]" if written else f"{notice}, before any answer."
        cut_off = reply.model_copy(update={"content": content, "invalid_tool_calls": []})
        return ModelResponse(
            result=[*response.result[:-1], cut_off],
            structured_response=response.structured_response,
        )

    @staticmethod
    def output_limit_stop(reply: AIMessage) -> str | None:
        """The provider's own stop reason, when it stopped ``reply`` at its output limit."""
        metadata = reply.response_metadata
        if metadata.get("status") == "incomplete":
            details = metadata.get("incomplete_details")
            if isinstance(details, dict) and details.get("reason") == "max_output_tokens":
                return "max_output_tokens"
        for key, stop in _OUTPUT_LIMIT_STOPS.items():
            if metadata.get(key) == stop:
                return stop
        return None


class _RunPromptCaching(AnthropicPromptCachingMiddleware):
    """Anthropic's prompt cache, less its breakpoint on the last tool.

    The tools come before the system prompt, so ``RunPromptMiddleware``'s breakpoints
    already cache them; the request's four go to the system prompt and conversation.
    """

    @property
    def name(self) -> str:
        """deepagents replaces its own prompt cache with the one of the same name."""
        return AnthropicPromptCachingMiddleware.__name__

    @staticmethod
    def _breaks_conversation(request: ModelRequest) -> bool:
        """Whether a message of the conversation carries its own cache breakpoint."""
        return any(
            isinstance(block, dict) and "cache_control" in block
            for message in request.messages
            if isinstance(message.content, list)
            for block in message.content
        )

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelCallResult:
        async def untagged_tools(tagged: ModelRequest) -> ModelResponse:
            settings = tagged.model_settings
            if self._breaks_conversation(tagged):
                settings = {k: v for k, v in settings.items() if k != "cache_control"}
            return await handler(tagged.override(tools=request.tools, model_settings=settings))

        return await super().awrap_model_call(request, untagged_tools)


def model_call_guards(
    timeout_seconds: float = MODEL_CALL_TIMEOUT_SECONDS, *, subagent: bool = False
) -> list[AgentMiddleware]:
    """Innermost around every model call, inside the middleware that picks its model.

    They see the model the call really goes to, the fallback included. Middleware
    owns retries for providers whose SDK retries are disabled. An agent's call that
    still fails raises, so ``call_with_fallback`` tries again and the run-complete
    webhook sees why; a subagent's timeout becomes its answer, as does a reply cut off
    at the output limit. The prompt cache replaces deepagents' own 5m one by name, in
    place.
    """
    return [
        *([SubagentTimeoutReplyMiddleware()] if subagent else []),
        OutputLimitMiddleware(subagent=subagent),
        ReplayableHistoryMiddleware(),
        ModelCallTimeoutMiddleware(timeout_seconds),
        ModelRetriesMiddleware(),
        (AnthropicPromptCachingMiddleware if subagent else _RunPromptCaching)(
            ttl=PROMPT_CACHE_TTL, unsupported_model_behavior="ignore"
        ),
    ]
