import logging
from collections.abc import Awaitable, Callable, Mapping
from typing import Literal, NotRequired

from langchain.agents.middleware.types import AgentState, ModelRequest, ModelResponse
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage
from langgraph.runtime import Runtime
from pydantic import BaseModel

from agent.middleware.trace import OpenSWEMiddleware
from agent.prompts import load_prompt, render_prompt

logger = logging.getLogger(__name__)

Route = Literal["fast", "balanced", "performance"]

_CLASSIFIER_PROMPT = load_prompt("model-selection.md")


class RouteDecision(BaseModel):
    model_route: Route


class ModelSelectionState(AgentState):
    model_route: NotRequired[Route]


class ModelSelectionMiddleware(OpenSWEMiddleware[ModelSelectionState]):
    state_schema = ModelSelectionState

    def __init__(
        self,
        models: Mapping[str, BaseChatModel],
        classifier: BaseChatModel,
        *,
        initial_plan_mode: bool = False,
    ) -> None:
        self._models = dict(models)
        self._classifier = classifier.with_structured_output(RouteDecision, method="json_schema")
        self._initial_plan_mode = initial_plan_mode

    async def abefore_agent(
        self,
        state: ModelSelectionState,
        runtime: Runtime,
    ) -> dict[str, Route]:
        del runtime
        route: Route = "performance" if self._initial_plan_mode else "balanced"
        if not self._initial_plan_mode:
            messages = state.get("messages", [])
            task = next(
                (
                    message.text
                    for message in reversed(messages)
                    if isinstance(message, HumanMessage)
                ),
                "",
            )
            try:
                decision = await self._classifier.ainvoke(
                    render_prompt("model-selection.md", task=task[-8_000:])
                )
                if isinstance(decision, RouteDecision):
                    route = decision.model_route
            except Exception:  # noqa: BLE001
                logger.exception("Model routing classifier failed")
        return {"model_route": route}

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        route = request.state.get("model_route", "balanced")
        model = self._models.get(route, self._models["balanced"])
        return await handler(request.override(model=model))
