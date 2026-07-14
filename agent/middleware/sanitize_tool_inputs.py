"""Sanitize tool input middleware.

Coerces malformed integer fields in read_file calls before they reach Pydantic
validation.  The LLM occasionally generates strings like ``'1, 80'`` or
``'170, "limit": 60'`` for integer parameters; we extract the leading digit
sequence so the call succeeds instead of burning an LLM turn on a retry.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable

from langchain.agents.middleware.types import AgentMiddleware, AgentState
from langchain_core.messages import ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

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


def _sanitize_read_file_args(args: dict) -> dict:
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


class SanitizeToolInputsMiddleware(AgentMiddleware):
    """Intercept read_file calls and coerce malformed integer parameters.

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

        name = tool_call.get("name")
        if name == "read_file":
            args = tool_call.get("args", {})
            sanitized_args = _sanitize_read_file_args(args)
            if sanitized_args is not args:
                new_tool_call = {**tool_call, "args": sanitized_args}
                return request.override(tool_call=new_tool_call)
        elif name == "execute":
            import os
            if os.getenv("SANDBOX_TYPE", "langsmith") != "langsmith":
                args = tool_call.get("args", {})
                command = args.get("command")
                if isinstance(command, str) and ("GH_TOKEN=dummy" in command or "GITHUB_TOKEN=dummy" in command):
                    # Strip the dummy token environment variables.
                    # This ensures we never format a live token into the command string.
                    # The commands will instead use the secure git credential helper and hosts.yml config.
                    import re
                    new_command = command
                    new_command = re.sub(r'\bGH_TOKEN=dummy\s*', '', new_command)
                    new_command = re.sub(r'\bGITHUB_TOKEN=dummy\s*', '', new_command)
                    if new_command != command:
                        new_tool_call = {**tool_call, "args": {**args, "command": new_command}}
                        return request.override(tool_call=new_tool_call)

        return request

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        return await handler(self._sanitize_request(request))
