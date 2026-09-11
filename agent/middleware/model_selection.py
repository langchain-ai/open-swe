"""Pre-routed mode: the agent sizes the task on the cheap model, then picks its route.

Every thread starts pre-routed on the ``fast`` profile. ``exit_pre_routed_mode``
writes ``model_route`` into state and the thread's stored settings; the decision
is final for the thread. Plan mode takes precedence: it runs on ``performance``
and ``exit_plan_mode`` carries the routing decision instead.

The tool list and system prompt are identical before and after the exit so a
fast→fast exit keeps the provider's prompt cache warm. Read-only discipline is
enforced by rejecting disallowed tool calls, not by hiding the tools.
"""

from collections.abc import Awaitable, Callable, Mapping
from typing import Any, NotRequired

from langchain.agents.middleware.types import (
    AgentState,
    ModelRequest,
    ModelResponse,
    ToolCallRequest,
)
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import ToolMessage
from langgraph.runtime import Runtime
from langgraph.types import Command

from agent.middleware.trace import OpenSWEMiddleware
from agent.utils.thread_settings import ModelRoute

EXIT_PRE_ROUTED_MODE_TOOL = "exit_pre_routed_mode"
PRE_ROUTED_MODE_TOOLS: frozenset[str] = frozenset(
    {EXIT_PRE_ROUTED_MODE_TOOL, "execute", "read_file", "ls", "glob"}
)


class ModelSelectionState(AgentState):
    model_route: NotRequired[ModelRoute]
    pre_routed: NotRequired[bool]
    plan_mode: NotRequired[bool]


class ModelSelectionMiddleware(OpenSWEMiddleware[ModelSelectionState]):
    state_schema = ModelSelectionState

    def __init__(
        self,
        models: Mapping[str, BaseChatModel],
        *,
        initial_route: ModelRoute | None = None,
    ) -> None:
        self._models = dict(models)
        self._initial_route = initial_route

    async def abefore_agent(
        self,
        state: ModelSelectionState,
        runtime: Runtime,
    ) -> dict[str, Any]:
        # Run state does not outlive the run; the committed route arrives from the
        # thread's stored settings via ``initial_route``.
        del runtime
        route = state.get("model_route") or self._initial_route
        if route is None:
            return {"pre_routed": True}
        return {"model_route": route, "pre_routed": False}

    def _model_for(self, state: Mapping[str, Any]) -> BaseChatModel:
        if state.get("plan_mode"):
            return self._models["performance"]
        if state.get("pre_routed"):
            return self._models["fast"]
        route = state.get("model_route", "balanced")
        return self._models.get(route, self._models["balanced"])

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        return await handler(request.override(model=self._model_for(request.state)))

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        rejection = _rejection(request)
        if rejection is not None:
            return ToolMessage(
                content=rejection, tool_call_id=request.tool_call["id"] or "", status="error"
            )
        return await handler(request)


def _rejection(request: ToolCallRequest) -> str | None:
    name = request.tool_call["name"]
    state = request.state
    if state.get("plan_mode"):
        if name == EXIT_PRE_ROUTED_MODE_TOOL:
            return "Plan mode is active; exit_plan_mode carries the routing decision."
        return None
    if state.get("pre_routed"):
        if name in PRE_ROUTED_MODE_TOOLS:
            return None
        return (
            f"`{name}` is unavailable in pre-routed mode. Size the task with `execute`, "
            "`read_file`, `ls`, and `glob`, then call `exit_pre_routed_mode` first."
        )
    if name == EXIT_PRE_ROUTED_MODE_TOOL:
        return "This thread is already routed; the decision is final."
    return None
