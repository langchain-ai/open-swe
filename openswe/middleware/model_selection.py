import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Annotated, Literal, NotRequired

from langchain.agents.middleware.types import AgentState, OmitFromOutput
from langchain_core.messages import HumanMessage
from langgraph.config import get_stream_writer
from langgraph.runtime import Runtime
from runtime_api.catalog import ROUTES, RouteName, kept_route
from runtime_api.provenance import Provenance

from openswe.input_messages import input_message_text
from openswe.middleware.trace import OpenSWEMiddleware
from openswe.prompts import prompt
from openswe.utils.jev import JevDecision, select_jev_choice

logger = logging.getLogger(__name__)

SelectedRoute = RouteName
PersistedRoute = SelectedRoute | Literal["fast_alt"]
RoutingMode = Literal["auto", "fast"]


def _latest_human_task(messages: Sequence[object]) -> str:
    """What a person last wrote; context and system messages around it are skipped."""
    for message in reversed(messages):
        if (
            isinstance(message, HumanMessage)
            and (provenance := Provenance.of(message)) is not None
            and provenance.human
            and (authored := input_message_text(message.content))
        ):
            return authored
    return ""


def _route_criteria() -> dict[RouteName, str]:
    return {route: prompt(f"model-selection/{route}") for route in ROUTES}


@dataclass(frozen=True)
class RouteSelection:
    route: SelectedRoute
    classifier: JevDecision | None = None

    @property
    def persist(self) -> bool:
        return self.classifier is None or self.classifier.outcome == "accepted"


async def _select_jev_route(task: str) -> RouteSelection:
    decision = JevDecision()
    route = await select_jev_choice(
        task,
        question="route",
        instructions=prompt("model-selection/instructions"),
        criteria=_route_criteria(),
        decision=decision,
    )
    return RouteSelection(route or "default", decision)


async def classify_route(messages: Sequence[object]) -> RouteSelection:
    """The route the person's latest request takes, as the classifier reads it."""
    return await _select_jev_route(_latest_human_task(messages)[-8_000:])


class ModelSelectionState(AgentState):
    model_route: NotRequired[PersistedRoute]
    requested_model: NotRequired[Annotated[str | None, OmitFromOutput]]
    requested_effort: NotRequired[Annotated[str | None, OmitFromOutput]]


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
    ) -> RouteSelection:
        """Select the model route for a turn."""
        if requested_model := state.get("requested_model"):
            self.use_requested_model(requested_model)
            return RouteSelection("default")
        if self._routing_mode is None:
            return RouteSelection("default")
        if model_route := kept_route(state.get("model_route")):
            return RouteSelection(model_route)
        if self._routing_mode == "fast":
            return RouteSelection("fast")
        return await classify_route(state.get("messages", []))

    async def abefore_model(
        self,
        state: ModelSelectionState,
        runtime: Runtime,
    ) -> dict[str, SelectedRoute]:
        del runtime
        selection = await self.select_route(state)
        if self._routing_mode == "auto" or state.get("requested_model"):
            _emit_routed_model(self._route_model_ids, selection.route)
        return {"model_route": selection.route} if selection.persist else {}
