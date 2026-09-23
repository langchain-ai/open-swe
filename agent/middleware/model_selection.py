import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any, Literal, NotRequired

from langchain.agents.middleware.types import AgentState, ModelRequest, ModelResponse
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage
from langgraph.config import get_stream_writer
from langgraph.runtime import Runtime
from pydantic import BaseModel

from agent.input_messages import input_message_text, message_sender_id
from agent.middleware.trace import OpenSWEMiddleware
from agent.prompts import load_prompt, render_prompt

logger = logging.getLogger(__name__)

Route = Literal["fast", "balanced", "performance"]
PersistedRoute = Route | Literal["fast_alt"]
RoutingMode = Literal["auto", "fast"]

_CLASSIFIER_PROMPT = load_prompt("model-selection.md")


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
    model_route: NotRequired[PersistedRoute]


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
    ) -> Route:
        """Select the model route for a turn."""
        if self._routing_mode == "fast":
            return "fast"
        if model_route := state.get("model_route"):
            return normalize_route(model_route)
        messages = state.get("messages", [])
        task = _latest_human_task(messages)
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
        return {"model_route": route}

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        route: PersistedRoute = request.state.get("model_route", "balanced")
        model = self._models.get(normalize_route(route)) or self._models.get("balanced")
        if model is None:
            model = self._models["balanced"]
        return await handler(request.override(model=model))
