"""Strip legacy ``function_call`` fields before Fireworks provider calls.

The LangSmith LLM Gateway enforces strict request-schema validation and rejects
messages carrying the legacy OpenAI ``function_call`` field
(``Extra inputs are not permitted, field: 'messages[N].function_call'``).
``langchain_fireworks`` forwards ``function_call`` from
``AIMessage.additional_kwargs`` whenever it is present — even when the message
also carries modern ``tool_calls`` — so a message that was originally produced
by (or deserialized from) a provider that populated the legacy field will break
every subsequent Fireworks turn once routed through the gateway.

This middleware drops the redundant legacy field from assistant messages before
they reach the Fireworks serializer, mirroring the thinking-block sanitizer.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelCallResult, ModelRequest, ModelResponse
from langchain_core.messages import AIMessage

try:
    from langchain_fireworks.chat_models import ChatFireworks
except ImportError:  # pragma: no cover
    ChatFireworks = None  # type: ignore[assignment, misc]


def _is_chat_fireworks(model: object) -> bool:
    if ChatFireworks is None:
        return False
    seen: set[int] = set()
    current = model
    for _ in range(10):
        if isinstance(current, ChatFireworks):
            return True
        current_id = id(current)
        if current_id in seen:
            return False
        seen.add(current_id)
        bound = getattr(current, "bound", None)
        if bound is None or bound is current:
            return False
        current = bound
    return False


def _sanitize_messages(messages: list[Any]) -> None:
    for message in messages:
        if not isinstance(message, AIMessage):
            continue
        additional_kwargs = message.additional_kwargs
        if not isinstance(additional_kwargs, dict) or "function_call" not in additional_kwargs:
            continue
        # Drop the legacy field; modern tool calls live in `tool_calls` /
        # `additional_kwargs["tool_calls"]`, which the Fireworks serializer
        # emits separately and the gateway accepts.
        additional_kwargs.pop("function_call", None)


class SanitizeFireworksMessagesMiddleware(AgentMiddleware):
    """Drop legacy ``function_call`` fields before Fireworks provider calls."""

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelCallResult:
        if _is_chat_fireworks(request.model):
            _sanitize_messages(request.messages)
        return handler(request)

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> Any:
        if _is_chat_fireworks(request.model):
            _sanitize_messages(request.messages)
        return await handler(request)
