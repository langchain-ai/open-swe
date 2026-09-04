import logging
import re
from collections.abc import Awaitable, Callable, Mapping

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage

logger = logging.getLogger(__name__)

_SMALL_TASK_PATTERN = re.compile(
    r"\b(explain|summari[sz]e|status|find|locate|rename|typo|format|lint|docs?|comment)\b",
    re.IGNORECASE,
)
_COMPLEX_TASK_PATTERN = re.compile(
    r"\b(plan|architect|design|migrat|security|auth|database|schema|distributed|"
    r"multi[- ]?(?:repo|package|service)|large[- ]scale|codebase[- ]wide|cross[- ]cutting|"
    r"refactor|investigat|root cause|performance|concurren|race condition)\w*\b",
    re.IGNORECASE,
)


def _message_text(message: HumanMessage) -> str:
    if isinstance(message.content, str):
        return message.content
    return " ".join(
        block.get("text", "")
        for block in message.content
        if isinstance(block, dict) and isinstance(block.get("text"), str)
    )


def task_complexity(request: ModelRequest) -> str:
    state = request.state if isinstance(request.state, Mapping) else {}
    if state.get("plan_mode") is True:
        return "complex"
    prompt = next(
        (
            _message_text(message)
            for message in reversed(request.messages)
            if isinstance(message, HumanMessage)
        ),
        "",
    )
    if len(prompt) >= 4_000 or _COMPLEX_TASK_PATTERN.search(prompt):
        return "complex"
    if len(prompt) <= 500 and _SMALL_TASK_PATTERN.search(prompt):
        return "small"
    return "medium"


class ModelSelectionMiddleware(AgentMiddleware):
    def __init__(self, models: Mapping[str, BaseChatModel]) -> None:
        self._models = dict(models)

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        complexity = task_complexity(request)
        model = self._models[complexity]
        logger.info("Selected model for task complexity", extra={"task_complexity": complexity})
        return await handler(request.override(model=model))
