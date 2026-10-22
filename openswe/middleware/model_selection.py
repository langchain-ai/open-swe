import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Annotated, Literal, NotRequired

from langchain.agents.middleware.types import (
    AgentState,
    ModelRequest,
    ModelResponse,
    OmitFromOutput,
)
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage
from langgraph.config import get_stream_writer
from langgraph.runtime import Runtime

from openswe.input_messages import input_message_text, message_sender_id
from openswe.middleware.trace import OpenSWEMiddleware
from openswe.prompts import prompt
from openswe.utils.jev import select_jev_choice

logger = logging.getLogger(__name__)

Route = Literal["fast", "balanced", "performance"]
SelectedRoute = Route | Literal["default"]
PersistedRoute = SelectedRoute | Literal["fast_alt"]
RoutingMode = Literal["auto", "fast"]


def _latest_human_task(messages: Sequence[object]) -> str:
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


ROUTES: tuple[Route, ...] = ("fast", "balanced", "performance")


def _route_criteria() -> dict[Route, str]:
    return {route: prompt(f"model-selection/{route}") for route in ROUTES}


async def _select_jev_route(task: str) -> SelectedRoute:
    return (
        await select_jev_choice(
            task,
            question="route",
            instructions=prompt("model-selection/instructions"),
            criteria=_route_criteria(),
        )
        or "default"
    )


class ModelSelectionState(AgentState):
    model_route: NotRequired[PersistedRoute]
    requested_model: NotRequired[Annotated[str | None, OmitFromOutput]]
    requested_effort: NotRequired[Annotated[str | None, OmitFromOutput]]


def normalize_route(route: PersistedRoute) -> SelectedRoute:
    return "fast" if route == "fast_alt" else route


async def _emit_routed_model(
    models: Mapping[str, BaseChatModel],
    route_model_ids: Mapping[str, str],
    route: SelectedRoute,
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
        default_model: BaseChatModel,
        *,
        route_model_ids: Mapping[str, str] | None = None,
        routing_mode: RoutingMode | None = "auto",
        requested_model_factory: Callable[[str, str | None], BaseChatModel] | None = None,
    ) -> None:
        self._models = {**models, "default": default_model}
        self._route_model_ids = dict(route_model_ids or {})
        self._routing_mode = routing_mode
        self._requested_model_factory = requested_model_factory
        self._requested_models: dict[tuple[str, str | None], BaseChatModel] = {}

    def use_requested_model(self, model_id: str, effort: str | None = None) -> None:
        if self._requested_model_factory is None:
            raise ValueError("Requested model selection is not enabled")
        key = (model_id, effort)
        if key not in self._requested_models:
            self._requested_models[key] = self._requested_model_factory(model_id, effort)
        self._models["default"] = self._requested_models[key]
        self._route_model_ids["default"] = model_id

    async def select_route(
        self,
        state: ModelSelectionState,
    ) -> SelectedRoute:
        """Select the model route for a turn."""
        if requested_model := state.get("requested_model"):
            if self._requested_model_factory is not None:
                self.use_requested_model(requested_model, state.get("requested_effort"))
                return "default"
        if self._routing_mode is None:
            return "default"
        if model_route := state.get("model_route"):
            return normalize_route(model_route)
        if self._routing_mode == "fast":
            return "fast"
        messages = state.get("messages", [])
        task = _latest_human_task(messages)[-8_000:]
        return await _select_jev_route(task)

    async def abefore_model(
        self,
        state: ModelSelectionState,
        runtime: Runtime,
    ) -> dict[str, SelectedRoute]:
        del runtime
        route = await self.select_route(state)
        if self._routing_mode == "auto" or state.get("requested_model"):
            await _emit_routed_model(self._models, self._route_model_ids, route)
        return {"model_route": route}

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        route: PersistedRoute = request.state.get("model_route", "default")
        model = self._models.get(normalize_route(route)) or self._models["default"]
        return await handler(request.override(model=model))
