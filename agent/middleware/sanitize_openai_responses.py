"""Middleware that removes stale OpenAI Responses reasoning references."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.messages import AIMessage

try:
    from langchain_openai import ChatOpenAI
except ImportError:  # pragma: no cover
    ChatOpenAI = None  # type: ignore[assignment, misc]

logger = logging.getLogger(__name__)


def _is_chat_openai(model: object) -> bool:
    if ChatOpenAI is None:
        return False
    seen: set[int] = set()
    current = model
    for _ in range(10):
        if isinstance(current, ChatOpenAI):
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


def _is_stale_reasoning_reference(block: dict[str, Any]) -> bool:
    if block.get("type") != "reasoning":
        return False
    if block.get("encrypted_content"):
        return False
    block_id = block.get("id")
    return isinstance(block_id, str) and block_id.startswith("rs_")


def _sanitize_messages(messages: list[Any]) -> None:
    removed = 0
    for message in messages:
        if not isinstance(message, AIMessage) or not isinstance(message.content, list):
            continue
        content = []
        for block in message.content:
            if isinstance(block, dict) and _is_stale_reasoning_reference(block):
                removed += 1
                continue
            content.append(block)
        if len(content) != len(message.content):
            message.content = content
    if removed:
        logger.warning("Removed %d stale OpenAI Responses reasoning reference(s)", removed)


class SanitizeOpenAIResponsesMiddleware(AgentMiddleware):
    """Drop non-replayable OpenAI Responses reasoning item references."""

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> Any:
        if _is_chat_openai(request.model):
            _sanitize_messages(request.messages)
        return await handler(request)
