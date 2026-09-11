"""Pre-routed mode: the agent sizes the task on the cheap model, then picks its route.

Every thread starts pre-routed. The model call runs on the ``fast`` profile with a
read-only tool set plus ``exit_pre_routed_mode``; that tool writes ``model_route``
into state, which is final for the thread. Plan mode takes precedence: it runs on
``performance`` and ``exit_plan_mode`` carries the routing decision instead.
"""

from collections.abc import Awaitable, Callable, Mapping
from typing import Any, NotRequired

from langchain.agents.middleware.types import AgentState, ModelRequest, ModelResponse
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import SystemMessage
from langgraph.runtime import Runtime

from agent.middleware.plan_mode import tool_name
from agent.middleware.trace import OpenSWEMiddleware
from agent.prompts import load_prompt
from agent.utils.model import ModelRoute

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

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        state = request.state
        if state.get("plan_mode"):
            return await handler(
                request.override(
                    model=self._models["performance"],
                    tools=_without(request.tools, {EXIT_PRE_ROUTED_MODE_TOOL}),
                )
            )
        if state.get("pre_routed"):
            return await handler(
                request.override(
                    model=self._models["fast"],
                    tools=[t for t in request.tools if tool_name(t) in PRE_ROUTED_MODE_TOOLS],
                    system_message=_with_section(
                        request.system_message, load_prompt("system/pre-routed-mode.md")
                    ),
                )
            )
        route = state.get("model_route", "balanced")
        return await handler(
            request.override(
                model=self._models.get(route, self._models["balanced"]),
                tools=_without(request.tools, {EXIT_PRE_ROUTED_MODE_TOOL}),
            )
        )


def _without(tools: list[Any], names: set[str]) -> list[Any]:
    return [t for t in tools if tool_name(t) not in names]


def _with_section(system_message: SystemMessage | None, section: str) -> SystemMessage:
    existing = system_message.text if system_message is not None else ""
    return SystemMessage(content=f"{existing}\n\n{section}" if existing else section)
