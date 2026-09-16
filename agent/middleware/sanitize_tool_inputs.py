"""Sanitize tool input middleware.

Coerces malformed integer fields in read_file calls before they reach Pydantic
validation.  The LLM occasionally generates strings like ``'1, 80'`` or
``'170, "limit": 60'`` for integer parameters; we extract the leading digit
sequence so the call succeeds instead of burning an LLM turn on a retry.
"""

import logging
import re
from collections.abc import Awaitable, Callable
from typing import Any, cast

from langchain.agents.middleware.types import AgentState
from langchain_core.messages import AIMessage, ToolCall, ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

from agent.middleware.trace import OpenSWEMiddleware

logger = logging.getLogger(__name__)

_READ_FILE_INT_FIELDS = ("offset", "limit")
_NUMBERED_READ_FILE_ROW = re.compile(r"\s*(\d+(?:\.\d+)?)  (.*)")


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


def _read_file_content(state: AgentState, file_path: str) -> str | None:
    """Return the latest numbered read output for *file_path* from state."""
    messages = state.get("messages", []) if isinstance(state, dict) else []
    pending_paths: dict[str, str] = {}
    content: str | None = None
    for message in messages:
        if isinstance(message, AIMessage):
            for tool_call in message.tool_calls:
                if tool_call.get("name") != "read_file":
                    continue
                call_id = tool_call.get("id")
                args = tool_call.get("args")
                if isinstance(call_id, str) and isinstance(args, dict):
                    read_path = args.get("file_path")
                    if isinstance(read_path, str):
                        pending_paths[call_id] = read_path
            continue
        if not isinstance(message, ToolMessage) or not isinstance(message.content, str):
            continue
        read_path = pending_paths.pop(message.tool_call_id, None)
        if read_path == file_path and message.status != "error":
            rows: list[str] = []
            for row in message.content.splitlines():
                match = _NUMBERED_READ_FILE_ROW.fullmatch(row)
                if match is None:
                    break
                rows.append(match.group(2))
            if rows:
                content = "\n".join(rows)
    return content


def _uniform_leading_whitespace(value: str) -> str | None:
    lines = value.splitlines()
    if not lines:
        return None
    prefixes = [line[: len(line) - len(line.lstrip(" \t"))] for line in lines]
    prefix = prefixes[0][: min(map(len, prefixes))]
    for line_prefix in prefixes[1:]:
        common_length = 0
        for expected, actual in zip(prefix, line_prefix, strict=False):
            if expected != actual:
                break
            common_length += 1
        prefix = prefix[:common_length]
    if len(prefix) < 2:
        return None
    return prefix


def _remove_line_prefix(value: str, prefix: str) -> str:
    return "".join(line.removeprefix(prefix) for line in value.splitlines(keepends=True))


def _sanitize_edit_file_args(args: dict[str, Any], state: AgentState) -> dict[str, Any]:
    """Repair a uniformly shifted edit anchor when prior read output confirms it."""
    old_string = args.get("old_string")
    new_string = args.get("new_string")
    file_path = args.get("file_path")
    if (
        not isinstance(old_string, str)
        or not isinstance(new_string, str)
        or not isinstance(file_path, str)
    ):
        return args
    content = _read_file_content(state, file_path)
    prefix = _uniform_leading_whitespace(old_string)
    if content is None or prefix is None:
        return args
    sanitized_old = _remove_line_prefix(old_string, prefix)
    if old_string in content or sanitized_old not in content:
        return args
    logger.warning(
        "Removing shared leading whitespace from edit_file anchors for %s",
        file_path,
    )
    sanitized = dict(args)
    sanitized["old_string"] = sanitized_old
    sanitized["new_string"] = _remove_line_prefix(new_string, prefix)
    return sanitized


class SanitizeToolInputsMiddleware(OpenSWEMiddleware):
    """Sanitize malformed read_file and edit_file parameters.

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
        elif tool_call.get("name") == "edit_file":
            sanitized_args = _sanitize_edit_file_args(args, request.state)
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
