"""Middleware that removes malformed Anthropic thinking blocks before model calls."""

from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import AIMessage

from agent.middleware.trace import OpenSWEMiddleware


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


def _remove_trailing_assistant_prefills(messages: list[Any]) -> None:
    while messages and isinstance(messages[-1], AIMessage) and not messages[-1].tool_calls:
        messages.pop()


class SanitizeThinkingBlocksMiddleware(OpenSWEMiddleware):
    """Drop empty Anthropic thinking blocks before provider validation."""

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> Any:
        if _is_chat_anthropic(request.model):
            messages = list(request.messages)
            _sanitize_messages(messages)
            _remove_trailing_assistant_prefills(messages)
            request = request.override(messages=messages)
        return await handler(request)
