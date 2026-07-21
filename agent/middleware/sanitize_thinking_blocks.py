"""Middleware that removes malformed Anthropic thinking blocks before model calls."""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import AIMessage

_SERIALIZED_EMPTY_REASONING_RE = re.compile(
    r'^\s*\{"reasoning"\s*:\s*""\s*,\s*"type"\s*:\s*"reasoning"\}\s*'
)


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


def _sanitize_message(message: Any) -> None:
    if not isinstance(message, AIMessage):
        return
    if isinstance(message.content, str):
        stripped = _SERIALIZED_EMPTY_REASONING_RE.sub("", message.content)
        if stripped != message.content:
            message.content = stripped
        return
    if not isinstance(message.content, list):
        return
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


def _sanitize_messages(messages: list[Any]) -> None:
    for message in messages:
        _sanitize_message(message)


class SanitizeThinkingBlocksMiddleware(AgentMiddleware):
    """Drop empty Anthropic thinking blocks and leaked reasoning prefixes."""

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> Any:
        if _is_chat_anthropic(request.model):
            _sanitize_messages(request.messages)
        response = await handler(request)
        result = getattr(response, "result", None)
        if isinstance(result, list):
            _sanitize_messages(result)
        return response
