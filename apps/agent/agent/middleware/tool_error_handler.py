"""Tool error handling middleware.

Wraps all tool calls in try/except so that unhandled exceptions are
returned as error ToolMessages instead of crashing the agent run.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable

from langchain.agents.middleware.types import (
    AgentMiddleware,
    AgentState,
)
from langchain_core.messages import ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

logger = logging.getLogger(__name__)


def _get_name(candidate: object) -> str | None:
    if not candidate:
        return None
    if isinstance(candidate, str):
        return candidate
    if isinstance(candidate, dict):
        name = candidate.get("name")
    else:
        name = getattr(candidate, "name", None)
    return name if isinstance(name, str) and name else None


def _extract_tool_name(request: ToolCallRequest | None) -> str | None:
    if request is None:
        return None
    for attr in ("tool_call", "tool_name", "name"):
        name = _get_name(getattr(request, attr, None))
        if name:
            return name
    return None


def _to_error_payload(e: Exception, request: ToolCallRequest | None = None) -> dict[str, str]:
    data: dict[str, str] = {
        "error": str(e),
        "error_type": e.__class__.__name__,
        "status": "error",
    }
    tool_name = _extract_tool_name(request)
    if tool_name:
        data["name"] = tool_name
    return data


def _get_tool_call_id(request: ToolCallRequest) -> str | None:
    if isinstance(request.tool_call, dict):
        return request.tool_call.get("id")
    return None


class ToolErrorMiddleware(AgentMiddleware):
    """Normalize tool execution errors into predictable payloads.

    Catches any exception thrown during a tool call and converts it into
    a ToolMessage with status="error" so the LLM can see the failure and
    self-correct, rather than crashing the entire agent run.
    """

    state_schema = AgentState

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command],
    ) -> ToolMessage | Command:
        try:
            return handler(request)
        except Exception as e: 
            logger.exception("Error during tool call handling; request=%r", request)
            data = _to_error_payload(e, request)
            return ToolMessage(
                content=json.dumps(data),
                tool_call_id=_get_tool_call_id(request),
                status="error",
            )

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        try:
            return await handler(request)
        except Exception as e:  
            logger.exception("Error during tool call handling; request=%r", request)
            data = _to_error_payload(e, request)
            return ToolMessage(
                content=json.dumps(data),
                tool_call_id=_get_tool_call_id(request),
                status="error",
            )
