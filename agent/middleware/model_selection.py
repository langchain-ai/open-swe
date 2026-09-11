import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any, Literal, NotRequired

from langchain.agents.middleware.types import AgentState, ModelRequest, ModelResponse
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, ToolMessage
from langgraph.runtime import Runtime
from pydantic import BaseModel

from agent.input_messages import input_message_text, message_sender_id
from agent.middleware.trace import OpenSWEMiddleware
from agent.prompts import load_prompt, render_prompt

logger = logging.getLogger(__name__)

Route = Literal["fast", "balanced", "performance"]

_CLASSIFIER_PROMPT = load_prompt("model-selection.md")
_PLAN_APPROVED_PREFIX = "Plan mode is now inactive because the plan was approved."


def _latest_human_task(messages: Sequence[Any]) -> str:
    """The user's own request, skipping injected context envelopes.

    Context blocks (sender metadata, dynamic context) are appended as
    ``HumanMessage``s after the real input, so the newest ``HumanMessage`` is
    usually machine-authored. Only ``kind="human"`` envelopes carry a request.
    """
    plain = ""
    for message in reversed(messages):
        if not isinstance(message, HumanMessage):
            continue
        content = message.content
        if message_sender_id(content, kind="human") is not None:
            if authored := input_message_text(content):
                return authored
            continue
        text = message.text
        if plain or not isinstance(text, str) or "<dynamic-context" in text:
            continue
        if "<input-message" not in text:
            plain = text
    return plain


class RouteDecision(BaseModel):
    model_route: Route


class ModelSelectionState(AgentState):
    model_route: NotRequired[Route]
    plan_mode: NotRequired[bool]


class ModelSelectionMiddleware(OpenSWEMiddleware[ModelSelectionState]):
    state_schema = ModelSelectionState

    def __init__(
        self,
        models: Mapping[str, BaseChatModel],
        classifier: BaseChatModel,
    ) -> None:
        self._models = dict(models)
        # `nostream` keeps the routing decision out of the user-facing message
        # stream; it stays visible in traces, unlike the offloading summarizer.
        hidden_classifier = classifier.model_copy(
            update={"tags": [*(classifier.tags or []), "nostream"]}
        )
        self._classifier = hidden_classifier.with_structured_output(
            RouteDecision, method="json_schema"
        )

    async def abefore_model(
        self,
        state: ModelSelectionState,
        runtime: Runtime,
    ) -> dict[str, Route]:
        del runtime
        if model_route := state.get("model_route"):
            return {"model_route": model_route}
        if state.get("plan_mode"):
            return {}
        messages = state.get("messages", [])
        approved_plan = next(
            (
                message.text
                for message in reversed(messages)
                if isinstance(message, ToolMessage)
                and message.text.startswith(_PLAN_APPROVED_PREFIX)
            ),
            "",
        )
        task = approved_plan or _latest_human_task(messages)
        route: Route = "balanced"
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
        route = (
            "performance"
            if request.state.get("plan_mode")
            else request.state.get("model_route", "balanced")
        )
        model = self._models.get(route, self._models["balanced"])
        return await handler(request.override(model=model))
