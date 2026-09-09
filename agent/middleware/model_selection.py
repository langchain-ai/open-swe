import logging
from collections.abc import Awaitable, Callable, Mapping
from typing import Literal, NotRequired

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import AgentState, ModelRequest, ModelResponse
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage
from langgraph.runtime import Runtime
from pydantic import BaseModel

logger = logging.getLogger(__name__)

Route = Literal["sol_medium", "terra_high", "luna_xhigh"]

_CLASSIFIER_PROMPT = """Choose the least expensive model profile likely to complete this Open SWE turn safely.

- luna_xhigh: direct questions, routine operations, evidence gathering, and small explicit changes with strong verification.
- terra_high: ordinary bug fixes, bounded investigations, multi-file implementation, and research synthesis.
- sol_medium: architecture, ambiguous requirements, subtle review, novel diagnosis, conflicting evidence, or consequential security/data decisions.

Return one model_route for the whole turn.

Current turn:
{task}
"""


class RouteDecision(BaseModel):
    model_route: Route


class ModelSelectionState(AgentState):
    model_route: NotRequired[Route]


class ModelSelectionMiddleware(AgentMiddleware[ModelSelectionState]):
    state_schema = ModelSelectionState

    def __init__(
        self,
        models: Mapping[Route, BaseChatModel],
        classifier: BaseChatModel,
        *,
        initial_plan_mode: bool = False,
    ) -> None:
        self._models = dict(models)
        self._classifier = classifier.with_structured_output(RouteDecision)
        self._initial_plan_mode = initial_plan_mode

    async def abefore_agent(
        self,
        state: ModelSelectionState,
        runtime: Runtime,
    ) -> dict[str, Route]:
        del runtime
        route: Route = "sol_medium" if self._initial_plan_mode else "terra_high"
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
                    _CLASSIFIER_PROMPT.format(task=task[-8_000:])
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
        route = request.state.get("model_route", "terra_high")
        model = self._models.get(route, self._models["terra_high"])
        return await handler(request.override(model=model))
