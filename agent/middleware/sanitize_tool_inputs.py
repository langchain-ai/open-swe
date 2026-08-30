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

from langchain.agents.middleware.types import AgentMiddleware, AgentState
from langchain_core.messages import ToolCall, ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

logger = logging.getLogger(__name__)

_READ_FILE_INT_FIELDS = ("offset", "limit")
_LINE_NUMBER_PREFIX = re.compile(r"^\s*\d+  ")


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


def _strip_line_number_prefixes(value: str) -> str | None:
    """Strip read_file line-number prefixes when every line has one."""
    lines = value.splitlines(keepends=True)
    if not lines or not all(_LINE_NUMBER_PREFIX.match(line) for line in lines):
        return None
    return "".join(_LINE_NUMBER_PREFIX.sub("", line, count=1) for line in lines)


def _indentation_normalized_match(old_string: str, file_content: str) -> str | None:
    """Return the unique file span matching old_string without indentation."""
    old_lines = old_string.splitlines(keepends=True)
    if not old_lines:
        return None
    normalized_old = [line.lstrip() for line in old_lines]
    file_lines = file_content.splitlines(keepends=True)
    matches = []
    for start in range(len(file_lines) - len(old_lines) + 1):
        span = file_lines[start : start + len(old_lines)]
        if [line.lstrip() for line in span] == normalized_old:
            matches.append("".join(span))
    return matches[0] if len(matches) == 1 else None


def _nearest_matching_lines(old_string: str, file_content: str) -> str:
    """Return file lines whose non-whitespace text resembles the anchor."""
    old_text = {line.strip() for line in old_string.splitlines() if line.strip()}
    candidates = []
    for line_number, line in enumerate(file_content.splitlines(), start=1):
        stripped = line.strip()
        if stripped and any(stripped in target or target in stripped for target in old_text):
            candidates.append(f"line {line_number}: {line!r}")
    if not candidates:
        return ""
    return "\nNearest matching lines:\n" + "\n".join(candidates[:5])


class SanitizeToolInputsMiddleware(AgentMiddleware):
    """Intercept malformed read_file and edit_file parameters.

    When the LLM produces a string value for an integer field (e.g.
    ``offset='1, 80'``), this middleware extracts the leading integer so that
    Pydantic validation passes rather than raising a ``ValidationError`` and
    forcing an unnecessary retry.
    """

    state_schema = AgentState

    async def _read_file_content(self, request: ToolCallRequest, file_path: str) -> str | None:
        """Read file content through the runtime's registered read_file tool."""
        read_tool = next(
            (tool for tool in request.runtime.tools if getattr(tool, "name", None) == "read_file"),
            None,
        )
        if read_tool is None:
            return None
        result = await read_tool.ainvoke({"file_path": file_path}, config=request.runtime.config)
        if (
            not isinstance(result, ToolMessage)
            or result.status == "error"
            or not isinstance(result.content, str)
        ):
            return None
        return "".join(
            _LINE_NUMBER_PREFIX.sub("", line, count=1)
            for line in result.content.splitlines(keepends=True)
        )

    async def _sanitize_edit_request(
        self, request: ToolCallRequest, args: dict[str, Any]
    ) -> tuple[ToolCallRequest, str | None]:
        file_content = await self._read_file_content(request, args.get("file_path", ""))
        if file_content is None or not isinstance(args.get("old_string"), str):
            return request, None
        old_string = args["old_string"]
        stripped = _strip_line_number_prefixes(old_string)
        if stripped is not None and stripped in file_content:
            old_string = stripped
        else:
            candidate = stripped if stripped is not None else old_string
            normalized = _indentation_normalized_match(candidate, file_content)
            if normalized is not None:
                old_string = normalized
        new_tool_call = cast(
            ToolCall, {**request.tool_call, "args": {**args, "old_string": old_string}}
        )
        return request.override(tool_call=new_tool_call), file_content

    def _sanitize_request(self, request: ToolCallRequest) -> ToolCallRequest:
        tool_call = request.tool_call
        if not isinstance(tool_call, dict) or tool_call.get("name") != "read_file":
            return request
        args = tool_call.get("args", {})
        if not isinstance(args, dict):
            return request
        sanitized_args = _sanitize_read_file_args(args)
        if sanitized_args is args:
            return request
        new_tool_call = cast(ToolCall, {**tool_call, "args": sanitized_args})
        return request.override(tool_call=new_tool_call)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        tool_call = request.tool_call
        if isinstance(tool_call, dict) and tool_call.get("name") == "edit_file":
            args = tool_call.get("args", {})
            if isinstance(args, dict):
                sanitized_request, file_content = await self._sanitize_edit_request(request, args)
                result = await handler(sanitized_request)
                if (
                    file_content is not None
                    and isinstance(result, ToolMessage)
                    and result.status == "error"
                    and "String not found in file" in str(result.content)
                ):
                    result = ToolMessage(
                        content=str(result.content)
                        + _nearest_matching_lines(
                            sanitized_request.tool_call["args"].get("old_string", ""), file_content
                        ),
                        name=result.name,
                        tool_call_id=result.tool_call_id,
                        status=result.status,
                    )
                return result
        return await handler(self._sanitize_request(request))
