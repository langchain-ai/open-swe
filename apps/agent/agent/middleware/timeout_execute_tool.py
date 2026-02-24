"""Tool middleware that wraps execute commands with a timeout."""

from __future__ import annotations

import logging
import re
import shlex
from collections.abc import Awaitable, Callable

from langchain.agents.middleware.types import AgentMiddleware, AgentState
from langchain_core.messages import ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 300
TIMEOUT_REGEX = re.compile(r"\btimeout\s+\d+(?:\.\d+)?\s*[smhd]?\b", re.IGNORECASE)


def _get_tool_name(request: ToolCallRequest) -> str | None:
    tool_call = request.tool_call
    if isinstance(tool_call, dict):
        return tool_call.get("name")
    return None


def _get_command_arg(request: ToolCallRequest) -> str | None:
    tool_call = request.tool_call
    if not isinstance(tool_call, dict):
        return None
    args = tool_call.get("args")
    if not isinstance(args, dict):
        return None
    command = args.get("command")
    return command if isinstance(command, str) else None


def _wrap_command(command: str) -> str:
    if TIMEOUT_REGEX.search(command):
        return command
    quoted = shlex.quote(command)
    return f"timeout {DEFAULT_TIMEOUT_SECONDS}s sh -c {quoted}"


def _overwrite_request_if_needed(request: ToolCallRequest) -> ToolCallRequest:
    if _get_tool_name(request) != "execute":
        return request

    command = _get_command_arg(request)
    if not command:
        return request

    wrapped = _wrap_command(command)
    if wrapped == command:
        return request

    tool_call = dict(request.tool_call)
    args = dict(tool_call.get("args", {}))
    args["command"] = wrapped
    tool_call["args"] = args
    logger.debug("Wrapped execute command with timeout")
    return request.override(tool_call=tool_call)


class TimeoutExecuteToolMiddleware(AgentMiddleware):
    """Ensure execute tool calls are wrapped with a timeout."""

    state_schema = AgentState

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command],
    ) -> ToolMessage | Command:
        return handler(_overwrite_request_if_needed(request))

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        return await handler(_overwrite_request_if_needed(request))
