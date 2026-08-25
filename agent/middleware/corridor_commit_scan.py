"""Prevent agent shell commands from bypassing Corridor commit scanning."""

import re
from collections.abc import Awaitable, Callable, Mapping

from langchain.agents.middleware.types import AgentMiddleware, AgentState
from langchain_core.messages import ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

from ..integrations.corridor_commit_scan import corridor_commit_scanning_enabled

_BYPASS = re.compile(
    r"(?:--no-verify\b|"
    r"\bgit\b[^\n;&|]*\bcommit\b[^\n;&|]*\s-n(?:\s|$)|"
    r"\bgit\b[^\n;&|]*-c\s*core\.hooksPath(?:=|\s)|"
    r"\b(?:git\s+)?config\b[^\n;&|]*(?:core\.hooksPath|CORRIDOR_COMMIT_SCANNING)|"
    r"\b(?:rm|unlink|mv|chmod|install|ln|truncate|tee)\b[^\n;&|]*open-swe/git-hooks)"
)


def _tool_call(request: ToolCallRequest) -> Mapping[str, object] | None:
    value = getattr(request, "tool_call", None)
    return value if isinstance(value, Mapping) else None


def _blocked(request: ToolCallRequest) -> ToolMessage:
    tool_call = _tool_call(request)
    tool_call_id = tool_call.get("id") if tool_call else ""
    return ToolMessage(
        content="Corridor commit scanning is enabled; this command would bypass its Git hook.",
        tool_call_id=tool_call_id if isinstance(tool_call_id, str) else "",
        status="error",
    )


class CorridorCommitScanMiddleware(AgentMiddleware):
    state_schema = AgentState

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        if not corridor_commit_scanning_enabled():
            return await handler(request)
        tool_call = _tool_call(request)
        name = tool_call.get("name") if tool_call else None
        args = tool_call.get("args") if tool_call else None
        command = args.get("command") if isinstance(args, Mapping) else None
        if name in {"execute", "background_execute"} and isinstance(command, str):
            if _BYPASS.search(command):
                return _blocked(request)
        return await handler(request)
