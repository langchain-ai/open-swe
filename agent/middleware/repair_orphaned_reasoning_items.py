"""Middleware that repairs orphaned OpenAI Responses-API function_call items.

The OpenAI Responses API requires that every ``function_call`` item emitted by a
reasoning model be accompanied by its paired ``reasoning`` item. When a run is
cancelled or the sandbox dies mid-turn, LangGraph can persist an ``AIMessage``
whose content carries a ``function_call`` block (``fc_...``) while the matching
``reasoning`` block (``rs_...``) is dropped on reconstruction. Every re-entry of
the thread then hits ``BadRequestError 400 ... Item 'fc_...' of type
'function_call' was provided without its required 'reasoning' item 'rs_...'`` —
permanently wedging the thread with the identical error.

``RepairOrphanedToolCallsMiddleware`` only repairs the tool_call↔tool_result
direction and ``SanitizeThinkingBlocksMiddleware`` only handles Anthropic
thinking blocks; neither enforces this pairing. This middleware scans the
outgoing message list before every OpenAI Responses-API model call and, for any
``AIMessage`` that carries ``function_call`` blocks but no ``reasoning`` block,
drops the orphaned ``function_call`` blocks, their ``tool_calls`` entries, and
any downstream ``ToolMessage`` tied to them so the request validates. Because the
poisoned state is already persisted, running on reconstruction lets existing
wedged threads self-heal on the next entry.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.messages import AIMessage, ToolMessage
from langchain_openai import ChatOpenAI

logger = logging.getLogger(__name__)


def _is_openai_responses_model(model: object) -> bool:
    """Return True for a ChatOpenAI bound to the Responses API."""
    seen: set[int] = set()
    current = model
    for _ in range(10):
        if isinstance(current, ChatOpenAI):
            return bool(getattr(current, "use_responses_api", False))
        current_id = id(current)
        if current_id in seen:
            return False
        seen.add(current_id)
        bound = getattr(current, "bound", None)
        if bound is None or bound is current:
            return False
        current = bound
    return False


def _orphaned_function_call_ids(message: AIMessage) -> set[str]:
    """Return function_call ``call_id``s on a message that lack a reasoning item."""
    if not isinstance(message.content, list):
        return set()
    has_reasoning = any(
        isinstance(block, dict) and block.get("type") == "reasoning" for block in message.content
    )
    if has_reasoning:
        return set()
    call_ids: set[str] = set()
    for block in message.content:
        if isinstance(block, dict) and block.get("type") == "function_call":
            call_id = block.get("call_id")
            if isinstance(call_id, str) and call_id:
                call_ids.add(call_id)
    return call_ids


def _repair_messages(messages: list[Any]) -> list[Any] | None:
    """Drop orphaned function_call blocks and their results; return new list or None."""
    orphaned_call_ids: set[str] = set()
    for message in messages:
        if isinstance(message, AIMessage):
            orphaned_call_ids |= _orphaned_function_call_ids(message)
    if not orphaned_call_ids:
        return None

    repaired: list[Any] = []
    for message in messages:
        if (
            isinstance(message, ToolMessage)
            and isinstance(message.tool_call_id, str)
            and message.tool_call_id in orphaned_call_ids
        ):
            continue
        if isinstance(message, AIMessage):
            _strip_orphaned_blocks(message, orphaned_call_ids)
        repaired.append(message)

    logger.warning(
        "Repaired %d orphaned function_call item(s) missing a reasoning item before model call",
        len(orphaned_call_ids),
    )
    return repaired


def _strip_orphaned_blocks(message: AIMessage, orphaned_call_ids: set[str]) -> None:
    """Remove orphaned function_call content blocks and tool_calls from a message."""
    if isinstance(message.content, list):
        message.content = [
            block
            for block in message.content
            if not (
                isinstance(block, dict)
                and block.get("type") == "function_call"
                and block.get("call_id") in orphaned_call_ids
            )
        ]
    if message.tool_calls:
        message.tool_calls = [
            tool_call
            for tool_call in message.tool_calls
            if tool_call.get("id") not in orphaned_call_ids
        ]


class RepairOrphanedReasoningItemsMiddleware(AgentMiddleware):
    """Drop OpenAI function_call items missing their reasoning item before model calls."""

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> Any:
        if _is_openai_responses_model(request.model):
            repaired = _repair_messages(request.messages)
            if repaired is not None:
                request.messages[:] = repaired
        return await handler(request)
