"""Middleware that removes stale OpenAI Responses reasoning references."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.messages import AIMessage, ToolMessage

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


def _sanitize_ai_message(message: AIMessage) -> tuple[int, set[str]]:
    """Drop stale reasoning blocks and their dependent function_call blocks.

    The Responses API requires a function_call item to be immediately preceded
    by the reasoning item that produced it. If that reasoning item can't be
    replayed (no encrypted_content to resume from), the function_call item
    must be dropped too, or the request is rejected as malformed. Returns the
    number of reasoning blocks removed and the set of ``call_id`` values for
    any function_call blocks dropped this way, so their corresponding
    ToolMessages can be removed as well.
    """
    content = []
    removed_reasoning = 0
    dropped_call_ids: set[str] = set()
    dropping = False
    for block in message.content:
        if isinstance(block, dict) and _is_stale_reasoning_reference(block):
            dropping = True
            removed_reasoning += 1
            continue
        if dropping and isinstance(block, dict) and block.get("type") == "function_call":
            call_id = block.get("call_id")
            if isinstance(call_id, str):
                dropped_call_ids.add(call_id)
            continue
        dropping = False
        content.append(block)
    if len(content) != len(message.content):
        message.content = content
    if dropped_call_ids and message.tool_calls:
        message.tool_calls = [
            tool_call
            for tool_call in message.tool_calls
            if tool_call.get("id") not in dropped_call_ids
        ]
    return removed_reasoning, dropped_call_ids


def _sanitize_messages(messages: list[Any]) -> None:
    removed_reasoning = 0
    dropped_call_ids: set[str] = set()
    for message in messages:
        if not isinstance(message, AIMessage) or not isinstance(message.content, list):
            continue
        message_removed, message_dropped = _sanitize_ai_message(message)
        removed_reasoning += message_removed
        dropped_call_ids |= message_dropped
    if dropped_call_ids:
        for index in range(len(messages) - 1, -1, -1):
            message = messages[index]
            if isinstance(message, ToolMessage) and message.tool_call_id in dropped_call_ids:
                del messages[index]
    if removed_reasoning:
        logger.warning(
            "Removed %d stale OpenAI Responses reasoning reference(s) and %d "
            "dependent function_call/tool result(s)",
            removed_reasoning,
            len(dropped_call_ids),
        )


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
