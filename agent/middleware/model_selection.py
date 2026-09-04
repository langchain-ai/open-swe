import hashlib
import json
import logging
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Literal, NotRequired

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import AgentState, ModelRequest, ModelResponse
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage
from langgraph.runtime import Runtime
from pydantic import BaseModel

logger = logging.getLogger(__name__)

_CLASSIFIER_PROMPT = """Classify the software-engineering task for model routing.

small: bounded, mechanical, read-only, formatting, documentation, or trivial edits
medium: normal implementation, debugging, tests, or a contained multi-file change
complex: ambiguous planning, architecture, broad migrations, security-sensitive work, difficult root-cause analysis, or large cross-cutting changes

Choose the least capable tier likely to complete the full task successfully. Return only the structured result.

Task:
{task}
"""


class RouteDecision(BaseModel):
    complexity: Literal["small", "medium", "complex"]


class ModelSelectionState(AgentState):
    model_route: NotRequired[Literal["small", "medium", "complex"]]
    model_route_for: NotRequired[str]


def _message_text(message: HumanMessage) -> str:
    if isinstance(message.content, str):
        return message.content
    return " ".join(
        block.get("text", "")
        for block in message.content
        if isinstance(block, dict) and isinstance(block.get("text"), str)
    )


def _latest_task(state: Mapping[str, Any]) -> tuple[str, str]:
    messages = state.get("messages")
    if not isinstance(messages, list):
        return "", ""
    for index in range(len(messages) - 1, -1, -1):
        message = messages[index]
        if isinstance(message, HumanMessage):
            task = _message_text(message)
            payload = {"index": index, "id": message.id, "task": task}
            fingerprint = hashlib.sha256(
                json.dumps(payload, sort_keys=True, default=str).encode()
            ).hexdigest()
            return task, fingerprint
    return "", ""


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

    async def _classify(self, task: str) -> Literal["small", "medium", "complex"]:
        if self._initial_plan_mode:
            return "complex"
        try:
            decision = await self._classifier.ainvoke(_CLASSIFIER_PROMPT.format(task=task[-8_000:]))
        except Exception:  # noqa: BLE001
            logger.exception("Model routing classifier failed")
            return "medium"
        if not isinstance(decision, RouteDecision):
            logger.warning("Model routing classifier returned an unexpected result")
            return "medium"
        return decision.complexity

    async def abefore_agent(
        self,
        state: ModelSelectionState,
        runtime: Runtime,
    ) -> dict[str, Any] | None:
        task, fingerprint = _latest_task(state)
        if state.get("model_route_for") == fingerprint and state.get("model_route") in self._models:
            return None
        return {
            "model_route": await self._classify(task),
            "model_route_for": fingerprint,
        }

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        route = request.state.get("model_route")
        if route not in self._models:
            route = "medium"
        logger.info("Selected model for task complexity", extra={"task_complexity": route})
        return await handler(request.override(model=self._models[route]))
