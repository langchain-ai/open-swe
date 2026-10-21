"""Drop history a provider would reject before the request reaches it.

- Fireworks: the LangSmith LLM Gateway validates request schemas strictly and
  rejects the legacy OpenAI ``function_call`` field, which ``langchain_fireworks``
  forwards from ``AIMessage.additional_kwargs`` even beside modern ``tool_calls``.
  A message once produced by (or deserialized from) a provider that set it would
  break every later Fireworks turn through the gateway.
- OpenAI Responses with ``store=False``: reasoning items carry ``rs_`` ids OpenAI
  cannot resolve unless their encrypted content rides along.
- Anthropic: an empty thinking block fails provider validation.
"""

from collections.abc import Awaitable, Callable
from importlib import import_module
from importlib.util import find_spec
from typing import Final

from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_anthropic import ChatAnthropic
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, AnyMessage
from langchain_openai import ChatOpenAI

from model_guards.guard import ModelGuard, underlying

# Optional: the MDA runtime does not install it.
_ChatFireworks: Final[type[BaseChatModel] | None] = (
    import_module("langchain_fireworks.chat_models").ChatFireworks
    if find_spec("langchain_fireworks") is not None
    else None
)


def _strip_function_calls(messages: list[AnyMessage]) -> None:
    for message in messages:
        if isinstance(message, AIMessage) and isinstance(message.additional_kwargs, dict):
            # Modern tool calls live in `tool_calls`, which the serializer emits separately.
            message.additional_kwargs.pop("function_call", None)


def _drop_unreplayable_reasoning(messages: list[AnyMessage]) -> list[AnyMessage]:
    sanitized: list[AnyMessage] = []
    for message in messages:
        if not isinstance(message, AIMessage) or not isinstance(message.content, list):
            sanitized.append(message)
            continue
        content = [
            block
            for block in message.content
            if not (
                isinstance(block, dict)
                and block.get("type") == "reasoning"
                and isinstance(block.get("id"), str)
                and block["id"].startswith("rs_")
                and not block.get("encrypted_content")
            )
        ]
        sanitized.append(
            message
            if len(content) == len(message.content)
            else message.model_copy(update={"content": content})
        )
    return sanitized


def _drop_empty_thinking(messages: list[AnyMessage]) -> None:
    for message in messages:
        if not isinstance(message, AIMessage) or not isinstance(message.content, list):
            continue
        content = [
            block
            for block in message.content
            if not (
                isinstance(block, dict)
                and block.get("type") == "thinking"
                and not block.get("thinking")
            )
        ]
        if len(content) != len(message.content):
            message.content = content


class SanitizeFireworksMessagesMiddleware(ModelGuard):
    """Drop legacy ``function_call`` fields before Fireworks provider calls."""

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        if _ChatFireworks is not None and underlying(request.model, _ChatFireworks) is not None:
            _strip_function_calls(request.messages)
        return await handler(request)


class SanitizeOpenAIResponsesMiddleware(ModelGuard):
    """Drop reasoning item IDs that OpenAI cannot resolve with ``store=False``."""

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        model = underlying(request.model, ChatOpenAI)
        if model is not None and model.store is False:
            request = request.override(messages=_drop_unreplayable_reasoning(request.messages))
        return await handler(request)


class SanitizeThinkingBlocksMiddleware(ModelGuard):
    """Drop empty Anthropic thinking blocks before provider validation."""

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        if underlying(request.model, ChatAnthropic) is not None:
            _drop_empty_thinking(request.messages)
        return await handler(request)
