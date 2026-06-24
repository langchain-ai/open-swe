"""Middleware that repairs orphaned tool calls before model calls.

When a run is cancelled or the sandbox dies mid-tool-call, LangGraph persists the
``AIMessage`` with the ``tool_call`` but never the matching ``ToolMessage``. On the
next run a fresh human message lands where the tool result should be, so the
provider rejects the request (Anthropic: ``messages.N: `tool_use` ids were found
without `tool_result` blocks``; OpenAI raises the equivalent). That permanently
wedges the thread — every retry hits the same error.

This middleware scans the outgoing message list and inserts a synthetic error
``ToolMessage`` immediately after any ``tool_call`` whose id has no corresponding
``ToolMessage``. The agent then sees the interrupted tool as a normal tool error
and can retry instead of dying.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelCallResult, ModelRequest, ModelResponse
from langchain_core.messages import AIMessage, ToolMessage

logger = logging.getLogger(__name__)

INTERRUPTED_TOOL_RECOVERY = "tool_call_interrupted"

_INTERRUPTED_TOOL_ERROR = (
    "The previous tool call did not complete — the run was interrupted (cancelled "
    "or the sandbox became unavailable) before a result was returned. No output was "
    "captured. Retry the tool call if you still need it; if repository files are "
    "missing, re-clone or reinitialize the workspace first."
)


def _iter_tool_calls(message: AIMessage) -> list[tuple[str, str | None]]:
    """Return ``(id, name)`` for each well-formed tool call on the message."""
    calls: list[tuple[str, str | None]] = []
    for tool_call in message.tool_calls or []:
        if isinstance(tool_call, dict):
            call_id = tool_call.get("id")
            name = tool_call.get("name")
        else:
            call_id = getattr(tool_call, "id", None)
            name = getattr(tool_call, "name", None)
        if isinstance(call_id, str) and call_id:
            calls.append((call_id, name if isinstance(name, str) and name else None))
    return calls


def _synthetic_tool_message(call_id: str, name: str | None) -> ToolMessage:
    payload: dict[str, str] = {
        "status": "error",
        "error_type": "InterruptedToolCall",
        "recovery": INTERRUPTED_TOOL_RECOVERY,
        "error": _INTERRUPTED_TOOL_ERROR,
    }
    if name:
        payload["name"] = name
    return ToolMessage(
        content=json.dumps(payload),
        tool_call_id=call_id,
        name=name,
        status="error",
    )


def _repair_messages(messages: list[Any]) -> list[Any] | None:
    """Insert synthetic results for orphaned tool calls; return new list or None."""
    satisfied = {
        message.tool_call_id
        for message in messages
        if isinstance(message, ToolMessage) and isinstance(message.tool_call_id, str)
    }

    repaired: list[Any] = []
    inserted = 0
    for message in messages:
        repaired.append(message)
        if not isinstance(message, AIMessage):
            continue
        for call_id, name in _iter_tool_calls(message):
            if call_id in satisfied:
                continue
            repaired.append(_synthetic_tool_message(call_id, name))
            satisfied.add(call_id)
            inserted += 1

    if not inserted:
        return None
    logger.warning("Repaired %d orphaned tool call(s) before model call", inserted)
    return repaired


class RepairOrphanedToolCallsMiddleware(AgentMiddleware):
    """Insert synthetic tool results for interrupted tool calls before model calls."""

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelCallResult:
        repaired = _repair_messages(request.messages)
        if repaired is not None:
            request.messages[:] = repaired
        return handler(request)

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> Any:
        repaired = _repair_messages(request.messages)
        if repaired is not None:
            request.messages[:] = repaired
        return await handler(request)
