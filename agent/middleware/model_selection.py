import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Annotated, Any, Literal, NotRequired

import httpx2
from langchain.agents.middleware.types import AgentState, ModelRequest, ModelResponse
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, ToolMessage
from langgraph.config import get_stream_writer
from langgraph.runtime import Runtime
from pydantic import BaseModel, Field

from agent.config import ENV
from agent.input_messages import input_message_text, message_sender_id
from agent.middleware.trace import OpenSWEMiddleware
from agent.prompts import load_prompt, render_prompt
from agent.utils.gateway import gateway_base_url

logger = logging.getLogger(__name__)

Route = Literal["fast", "balanced", "performance"]
PersistedRoute = Route | Literal["fast_alt"]
RoutingMode = Literal["auto", "performance"]

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


class SemifChoice(BaseModel):
    type: Literal["choice"]
    choice: Route
    confidence: Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]
    probabilities: dict[Route, Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]]


class SemifAnswers(BaseModel):
    route: SemifChoice


class SemifResponse(BaseModel):
    answers: SemifAnswers


async def _select_semif_route(task: str) -> Route | None:
    api_key = ENV.LANGSMITH_GATEWAY_API_KEY.optional() or ENV.LANGSMITH_API_KEY.optional()
    if not api_key:
        logger.info("SemIf routing has no gateway key; using fallback classifier")
        return None
    try:
        async with httpx2.AsyncClient(timeout=3.0) as client:
            response = await client.post(
                f"{gateway_base_url()}/v1/systemone",
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "model": "semif-qwen3.5-4b",
                    "state": task,
                    "questions": {
                        "route": {
                            "type": "choice",
                            "instructions": render_prompt("model-selection.md", task=""),
                            "criteria": dict.fromkeys(("fast", "balanced", "performance")),
                        }
                    },
                },
            )
            response.raise_for_status()
            answer = SemifResponse.model_validate(response.json()).answers.route
        probabilities = answer.probabilities
        if (
            set(probabilities) != {"fast", "balanced", "performance"}
            or abs(sum(probabilities.values()) - 1) > 0.01
            or probabilities[answer.choice] != max(probabilities.values())
        ):
            raise ValueError("Invalid SemIf routing probabilities")
        if answer.confidence < 0.6:
            logger.info(
                "SemIf routing confidence below threshold; using fallback classifier",
                extra={"confidence": answer.confidence},
            )
            return None
        return answer.choice
    except Exception:
        logger.exception("SemIf routing failed; using fallback classifier")
        return None


class ModelSelectionState(AgentState):
    model_route: NotRequired[PersistedRoute]
    plan_mode: NotRequired[bool]


def normalize_route(route: PersistedRoute) -> Route:
    return "fast" if route == "fast_alt" else route


async def _emit_routed_model(
    models: Mapping[str, BaseChatModel],
    route_model_ids: Mapping[str, str],
    route: Route,
) -> None:
    """Stream the routed model's id so the UI can show it next to `Auto`."""
    model_id = route_model_ids.get(route)
    if model_id is None:
        model = models.get(route)
        model_id = getattr(model, "model_id", None)
    if not isinstance(model_id, str) or not model_id:
        return
    try:
        get_stream_writer()({"type": "model_routed", "route": route, "model_id": model_id})
    except Exception:
        # Routing display is cosmetic; never fail a run over it.
        logger.debug("Failed to emit model_routed event", exc_info=True)


class ModelSelectionMiddleware(OpenSWEMiddleware[ModelSelectionState]):
    state_schema = ModelSelectionState

    def __init__(
        self,
        models: Mapping[str, BaseChatModel],
        classifier: BaseChatModel,
        *,
        route_model_ids: Mapping[str, str] | None = None,
        routing_mode: RoutingMode = "auto",
    ) -> None:
        self._models = dict(models)
        self._route_model_ids = dict(route_model_ids or {})
        self._routing_mode = routing_mode
        # `nostream` keeps the routing decision out of the user-facing message
        # stream; it stays visible in traces, unlike the offloading summarizer.
        hidden_classifier = classifier.model_copy(
            update={"tags": [*(classifier.tags or []), "nostream"]}
        )
        self._classifier = hidden_classifier.with_structured_output(
            RouteDecision, method="json_schema"
        )

    async def select_route(
        self,
        state: ModelSelectionState,
        *,
        plan_mode: bool | None = None,
    ) -> Route:
        """Select the model route for a turn."""
        if state.get("plan_mode") if plan_mode is None else plan_mode:
            return "performance"
        if model_route := state.get("model_route"):
            return normalize_route(model_route)
        if self._routing_mode == "performance":
            return "performance"
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
        task = (approved_plan or _latest_human_task(messages))[-8_000:]
        if semif_route := await _select_semif_route(task):
            return semif_route
        route: Route = "balanced"
        try:
            decision = await self._classifier.ainvoke(
                render_prompt("model-selection.md", task=task[-8_000:])
            )
            if isinstance(decision, RouteDecision):
                route = decision.model_route
        except Exception:  # noqa: BLE001
            logger.exception("Model routing classifier failed")
        return route

    async def abefore_model(
        self,
        state: ModelSelectionState,
        runtime: Runtime,
    ) -> dict[str, Route]:
        del runtime
        route = await self.select_route(state)
        if self._routing_mode == "auto":
            await _emit_routed_model(self._models, self._route_model_ids, route)
        if state.get("plan_mode"):
            return {}
        return {"model_route": route}

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        route: PersistedRoute = (
            "performance"
            if request.state.get("plan_mode")
            else request.state.get("model_route", "balanced")
        )
        model = self._models.get(normalize_route(route)) or self._models.get("balanced")
        if model is None:
            model = self._models["balanced"]
        return await handler(request.override(model=model))
