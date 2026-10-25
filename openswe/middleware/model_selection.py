import logging
from collections.abc import Mapping, Sequence
from typing import Annotated, Literal, NotRequired

from langchain.agents.middleware.types import AgentState, OmitFromOutput
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


async def classify_route(messages: Sequence[object]) -> SelectedRoute:
    """The route the person's latest request takes, as the classifier reads it."""
    return await _select_jev_route(_latest_human_task(messages)[-8_000:])


class ModelSelectionState(AgentState):
    model_route: NotRequired[PersistedRoute]
    requested_model: NotRequired[Annotated[str | None, OmitFromOutput]]
    requested_effort: NotRequired[Annotated[str | None, OmitFromOutput]]


def normalize_route(route: PersistedRoute) -> SelectedRoute:
    return "fast" if route == "fast_alt" else route


def _emit_routed_model(route_model_ids: Mapping[str, str], route: SelectedRoute) -> None:
    """Stream the routed model's id so the UI can show it next to `Auto`."""
    model_id = route_model_ids.get(route)
    if not model_id:
        return
    try:
        get_stream_writer()({"type": "model_routed", "route": route, "model_id": model_id})
    except Exception:
        # Routing display is cosmetic; never fail a run over it.
        logger.debug("Failed to emit model_routed event", exc_info=True)


class ModelSelectionMiddleware(OpenSWEMiddleware[ModelSelectionState]):
    """Pick the route of each model call; the run's model choice reads it from state.

    MDA makes the call, so this keeps no models: only the id each route stands for.
    """

    state_schema = ModelSelectionState

    def __init__(
        self,
        route_model_ids: Mapping[str, str],
        *,
        routing_mode: RoutingMode | None = "auto",
    ) -> None:
        self._route_model_ids = dict(route_model_ids)
        self._routing_mode = routing_mode

    def use_requested_model(self, model_id: str) -> None:
        self._route_model_ids["default"] = model_id

    async def select_route(
        self,
        state: ModelSelectionState,
    ) -> SelectedRoute:
        """Select the model route for a turn."""
        if requested_model := state.get("requested_model"):
            self.use_requested_model(requested_model)
            return "default"
        if self._routing_mode is None:
            return "default"
        if model_route := state.get("model_route"):
            return normalize_route(model_route)
        if self._routing_mode == "fast":
            return "fast"
        return await classify_route(state.get("messages", []))

    async def abefore_model(
        self,
        state: ModelSelectionState,
        runtime: Runtime,
    ) -> dict[str, SelectedRoute]:
        del runtime
        route = await self.select_route(state)
        if self._routing_mode == "auto" or state.get("requested_model"):
            _emit_routed_model(self._route_model_ids, route)
        return {"model_route": route}
