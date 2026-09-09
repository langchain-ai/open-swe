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

Route = Literal["glm_flash", "sol_medium", "astra_low"]

_CLASSIFIER_PROMPT = """Choose one fixed model profile for this Open SWE turn. Use the least expensive profile likely to complete the whole turn safely.

Profiles, from least to most capable and expensive:

1. glm_flash
- Use for direct lookup, extraction, status checks, test or log collection, mechanical PR or release operations, and localized changes with explicit targets and strong verification.
- High effort increases persistence, not the base model's capability ceiling.

2. sol_medium
- Use for ordinary bug fixes, bounded investigations, multi-file implementation, research synthesis, semantic PR maintenance, and partially specified localized work.

3. astra_low
- Use for architecture or design, requirements disambiguation, subtle semantic review, novel root-cause reasoning, conflicting evidence, cross-component or multi-repository judgment, and high-stakes decisions.
- Low is its configured reasoning effort; it remains the highest-capability profile.

Explicit targets, clear acceptance criteria, reversibility, and strong tests lower the required capability. Ambiguous requirements, weak verification, architectural tradeoffs, broad scope, consequential security or data work, and conflicting assumptions raise it. Prompt length and eventual runtime are not difficulty signals.

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
        models: Mapping[str, BaseChatModel],
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
        route: Route = "astra_low" if self._initial_plan_mode else "sol_medium"
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
        route = request.state.get("model_route", "sol_medium")
        model = self._models.get(route, self._models["sol_medium"])
        return await handler(request.override(model=model))
