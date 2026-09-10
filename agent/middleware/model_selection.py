import logging
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Literal, NotRequired

from langchain.agents.middleware.types import AgentState, ModelRequest, ModelResponse
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import SystemMessage
from langchain_core.tools import BaseTool

from agent.middleware.trace import OpenSWEMiddleware
from agent.prompts import load_prompt

logger = logging.getLogger(__name__)

Route = Literal["fast", "balanced", "performance"]

_ROUTING_PROMPT = load_prompt("system/routing-mode-active.md")
_ROUTING_TOOLS = frozenset(
    {
        "execute",
        "exit_routing_mode",
        "fetch_url",
        "glob",
        "ls",
        "read_file",
        "slack_add_reaction",
        "slack_read_thread_messages",
        "slack_thread_reply",
        "web_search",
    }
)


class ModelSelectionState(AgentState):
    routing_mode: NotRequired[bool]
    model_route: NotRequired[Route]


def _tool_name(tool: BaseTool | dict[str, Any] | Any) -> str | None:
    if isinstance(tool, dict):
        name = tool.get("name")
        return name if isinstance(name, str) else None
    name = getattr(tool, "name", None)
    return name if isinstance(name, str) else None


class ModelSelectionMiddleware(OpenSWEMiddleware[ModelSelectionState]):
    state_schema = ModelSelectionState

    def __init__(self, models: Mapping[str, BaseChatModel]) -> None:
        self._models = dict(models)

    def before_agent(self, state: Any, runtime: Any) -> dict[str, Any]:  # noqa: ARG002
        return {"routing_mode": True, "model_route": "fast"}

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        routing = request.state.get("routing_mode") is not False
        if routing:
            tools = [tool for tool in request.tools if _tool_name(tool) in _ROUTING_TOOLS]
            existing = request.system_message.text if request.system_message is not None else ""
            prompt = f"{_ROUTING_PROMPT}\n\n{existing}" if existing else _ROUTING_PROMPT
            request = request.override(
                model=self._models["fast"],
                system_message=SystemMessage(content=prompt),
                tools=tools,
            )
        else:
            route = request.state.get("model_route", "balanced")
            model = self._models.get(route, self._models["balanced"])
            tools = [tool for tool in request.tools if _tool_name(tool) != "exit_routing_mode"]
            request = request.override(model=model, tools=tools)
        logger.info(
            "Selected model route",
            extra={"model_route": request.state.get("model_route"), "routing_mode": routing},
        )
        return await handler(request)
