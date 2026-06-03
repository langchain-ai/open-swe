"""Middleware that removes malformed Anthropic thinking blocks before model calls."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelCallResult, ModelRequest, ModelResponse
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import AIMessage


def _is_chat_anthropic(model: object) -> bool:
    seen: set[int] = set()
    current = model
    for _ in range(10):
        if isinstance(current, ChatAnthropic):
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


class SanitizeThinkingBlocksMiddleware(AgentMiddleware):
    """Drop empty Anthropic thinking blocks before provider validation."""

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelCallResult:
        if _is_chat_anthropic(request.model):
            _sanitize_messages(request.messages)
        return handler(request)

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> Any:
        if _is_chat_anthropic(request.model):
            _sanitize_messages(request.messages)
        return await handler(request)
