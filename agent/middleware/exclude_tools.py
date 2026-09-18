"""Hide named tools from the model without rebuilding the agent.

This provides per-agent tool filtering after Deep Agents injects its built-in
tools. It mirrors Deep Agents' private `_ToolExclusionMiddleware` without
depending on a private import path.
"""

from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware.types import (
    AgentState,
    ModelRequest,
    ModelResponse,
)
from langchain.tools.tool_node import ToolCallRequest
from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool
from langgraph.types import Command

from agent.middleware.trace import OpenSWEMiddleware
from agent.prompts import load_prompt


def _tool_name(tool: BaseTool | dict[str, Any] | Any) -> str | None:
    if isinstance(tool, dict):
        name = tool.get("name")
        return name if isinstance(name, str) else None
    name = getattr(tool, "name", None)
    return name if isinstance(name, str) else None


class ExcludeToolsMiddleware(OpenSWEMiddleware):
    """Strip named tools from each model request.

    Place this AFTER tool-injecting middleware (FilesystemMiddleware,
    SubAgentMiddleware) so it can remove middleware-injected tools too.
    """

    state_schema = AgentState

    def __init__(self, *, excluded: frozenset[str], blocked: frozenset[str] = frozenset()) -> None:
        self._excluded = excluded
        self._blocked = blocked

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        if request.tool_call["name"] in self._blocked:
            return ToolMessage(
                content=load_prompt("tools/subagents-disabled.md"),
                tool_call_id=request.tool_call["id"],
                status="error",
            )
        return await handler(request)

    def _filter(self, request: ModelRequest) -> ModelRequest:
        if not self._excluded:
            return request
        filtered = [t for t in request.tools if _tool_name(t) not in self._excluded]
        if len(filtered) == len(request.tools):
            return request
        return request.override(tools=filtered)

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        return await handler(self._filter(request))
