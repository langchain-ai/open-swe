"""Sanitize malformed tool input before validation or remote dispatch."""

import logging
import re
from collections.abc import Awaitable, Callable
from typing import Any, cast

from langchain.agents.middleware.types import AgentState
from langchain_core.messages import ToolCall, ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

from agent.middleware.trace import OpenSWEMiddleware

logger = logging.getLogger(__name__)

_READ_FILE_INT_FIELDS = ("offset", "limit")


def _coerce_int(value: object) -> int | None:
    """Extract the first integer from *value* if it is a non-integer string.

    Returns the parsed integer, or ``None`` if no leading digits are found.
    If *value* is already an ``int`` (or ``None``), returns it unchanged.
    """
    if value is None or isinstance(value, int):
        return value
    if isinstance(value, str):
        match = re.match(r"\s*(\d+)", value)
        if match:
            return int(match.group(1))
        return None
    return None


def _sanitize_read_file_args(args: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of *args* with integer fields coerced where needed."""
    sanitized = dict(args)
    for field in _READ_FILE_INT_FIELDS:
        if field in sanitized:
            original = sanitized[field]
            coerced = _coerce_int(original)
            if coerced is not None and coerced != original:
                logger.warning("Coercing read_file.%s from %r to %d", field, original, coerced)
                sanitized[field] = coerced
    return sanitized


def _remove_empty_mcp_values(value: object) -> object:
    if isinstance(value, dict):
        return {
            key: _remove_empty_mcp_values(item)
            for key, item in value.items()
            if not (isinstance(item, str) and not item.strip())
        }
    if isinstance(value, list):
        return [_remove_empty_mcp_values(item) for item in value]
    return value


def _sanitize_mcp_args(args: dict[str, Any]) -> dict[str, Any]:
    """Remove empty MCP arguments and invalid Linear status metadata."""
    sanitized = cast(dict[str, Any], _remove_empty_mcp_values(args))
    status_update_id = sanitized.get("statusUpdateId")
    if not isinstance(status_update_id, str) or not status_update_id.strip():
        sanitized.pop("statusUpdateType", None)
    return sanitized


def _is_mcp_tool(tool: object) -> bool:
    tool_type = type(tool)
    return tool_type.__module__ == "agent.mcp.runtime" and tool_type.__name__ == "_MCPTool"


class SanitizeToolInputsMiddleware(OpenSWEMiddleware):
    """Sanitize malformed read_file and MCP tool parameters.

    When the LLM produces a string value for an integer field (e.g.
    ``offset='1, 80'``), this middleware extracts the leading integer so that
    Pydantic validation passes rather than raising a ``ValidationError`` and
    forcing an unnecessary retry.
    """

    state_schema = AgentState

    def _sanitize_request(self, request: ToolCallRequest) -> ToolCallRequest:
        tool_call = request.tool_call
        if not isinstance(tool_call, dict):
            return request
        args = tool_call.get("args", {})
        if not isinstance(args, dict):
            return request
        if tool_call.get("name") == "read_file":
            sanitized_args = _sanitize_read_file_args(args)
        elif _is_mcp_tool(request.tool):
            sanitized_args = _sanitize_mcp_args(args)
        else:
            return request
        if sanitized_args is args:
            return request
        new_tool_call = cast(ToolCall, {**tool_call, "args": sanitized_args})
        return request.override(tool_call=new_tool_call)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        return await handler(self._sanitize_request(request))
