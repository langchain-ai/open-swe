import logging
from collections.abc import Awaitable, Callable, Mapping
from typing import Literal

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage
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


def _message_text(message: HumanMessage) -> str:
    if isinstance(message.content, str):
        return message.content
    return " ".join(
        block.get("text", "")
        for block in message.content
        if isinstance(block, dict) and isinstance(block.get("text"), str)
    )


def _latest_task(request: ModelRequest) -> str:
    return next(
        (
            _message_text(message)
            for message in reversed(request.messages)
            if isinstance(message, HumanMessage)
        ),
        "",
    )


class ModelSelectionMiddleware(AgentMiddleware):
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
        self._task: str | None = None
        self._complexity: Literal["small", "medium", "complex"] | None = None

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

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        task = _latest_task(request)
        if task != self._task or self._complexity is None:
            self._task = task
            self._complexity = await self._classify(task)
        logger.info(
            "Selected model for task complexity",
            extra={"task_complexity": self._complexity},
        )
        return await handler(request.override(model=self._models[self._complexity]))
