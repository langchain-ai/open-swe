import logging
from collections.abc import Callable, Mapping, Sequence
from typing import Annotated, Literal, NotRequired, cast

from langchain.agents.middleware import (
    ModelRoutingConfig,
    ModelRoutingInput,
    ModelRoutingMiddleware,
    ModelRoutingState,
)
from langchain.agents.middleware.types import (
    OmitFromOutput,
)
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableLambda
from langgraph.config import get_stream_writer
from langgraph.runtime import Runtime

from agent.input_messages import input_message_text, message_sender_id
from agent.middleware.trace import SCRUBBED_TRACE_POLICY
from agent.prompts import prompt
from agent.utils.jev import select_jev_choice

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


async def _classify_route(inputs: ModelRoutingInput) -> str:
    return (
        await select_jev_choice(
            inputs["messages"][0].text,
            question="route",
            instructions=inputs["system_prompt"],
            criteria={route: inputs["criteria"][route] for route in ROUTES},
        )
        or "default"
    )


def _routing_messages(state: ModelRoutingState) -> list[HumanMessage]:
    return [HumanMessage(content=_latest_human_task(state["messages"])[-8_000:])]


class ModelSelectionState(ModelRoutingState):
    requested_model: NotRequired[Annotated[str | None, OmitFromOutput]]


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


class ModelSelectionMiddleware(ModelRoutingMiddleware):
    state_schema = ModelSelectionState
    trace_policy = SCRUBBED_TRACE_POLICY

    def __init__(
        self,
        models: Mapping[str, BaseChatModel],
        default_model: BaseChatModel,
        *,
        route_model_ids: Mapping[str, str] | None = None,
        routing_mode: RoutingMode | None = "auto",
        requested_model_factory: Callable[[str], BaseChatModel] | None = None,
    ) -> None:
        configs: dict[str, ModelRoutingConfig] = {
            route: {
                "model": models.get(route, default_model),
                "criteria": prompt(f"model-selection/{route}"),
            }
            for route in ROUTES
        }
        configs["default"] = {"model": default_model, "criteria": ""}
        super().__init__(
            models=configs,
            decision_model=RunnableLambda(_classify_route),
            system_prompt=prompt("model-selection/instructions"),
            input_extractor=_routing_messages,
            fallback_route="default",
        )
        self._route_model_ids = dict(route_model_ids or {})
        self._routing_mode = routing_mode
        self._requested_model_factory = requested_model_factory
        self._requested_models: dict[str, BaseChatModel] = {}

    def use_requested_model(self, model_id: str) -> None:
        if self._requested_model_factory is None:
            raise ValueError("Requested model selection is not enabled")
        if model_id not in self._requested_models:
            self._requested_models[model_id] = self._requested_model_factory(model_id)
        self.models["default"] = self._requested_models[model_id]
        self._route_model_ids["default"] = model_id

    def before_model(self, state: ModelRoutingState, runtime: Runtime) -> dict[str, str]:
        raise NotImplementedError("Open SWE model routing requires async execution")

    async def abefore_model(self, state: ModelRoutingState, runtime: Runtime) -> dict[str, str]:
        routing_state = state.copy()
        requested_model = state.get("requested_model")
        if isinstance(requested_model, str) and self._requested_model_factory is not None:
            self.use_requested_model(requested_model)
            routing_state["model_route"] = "default"
        elif self._routing_mode is None:
            routing_state["model_route"] = "default"
        elif state.get("model_route") == "fast_alt":
            routing_state["model_route"] = "fast"
        elif self._routing_mode == "fast" and not state.get("model_route"):
            routing_state["model_route"] = "fast"
        update = await super().abefore_model(routing_state, runtime)
        if self._routing_mode == "auto" or requested_model:
            await _emit_routed_model(
                self.models, self._route_model_ids, cast(SelectedRoute, update["model_route"])
            )
        return update
