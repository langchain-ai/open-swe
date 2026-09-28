import logging
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from contextvars import ContextVar
from typing import Any, Literal, NotRequired

import httpx2
from langchain.agents.middleware.types import AgentState, ModelRequest, ModelResponse
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage
from langchain_typesafe import Choice, ClassifierRequest, TypeSafeClassifier
from langchain_typesafe.client import TypeSafeAPITimeoutError
from langgraph.config import get_stream_writer
from langgraph.runtime import Runtime
from pydantic import BaseModel

from agent.config import ENV
from agent.input_messages import input_message_text, message_sender_id
from agent.middleware.trace import OpenSWEMiddleware
from agent.prompts import prompt
from agent.utils.gateway import gateway_base_url

logger = logging.getLogger(__name__)
_ROUTE_FALLBACK = ContextVar("model_route_fallback", default=False)

Route = Literal["fast", "balanced", "performance"]
SelectedRoute = Route | Literal["default"]
PersistedRoute = SelectedRoute | Literal["fast_alt"]
RoutingMode = Literal["auto", "fast"]


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


ROUTES: tuple[Route, ...] = ("fast", "balanced", "performance")


def _route_criteria() -> dict[str, str]:
    return {route: prompt(f"model-selection/{route}") for route in ROUTES}


async def _select_jev_route(task: str) -> SelectedRoute:
    _ROUTE_FALLBACK.set(False)
    typesafe_key = ENV.TYPESAFE_API_KEY.optional()
    gateway_key = ENV.LANGSMITH_GATEWAY_API_KEY.optional() or ENV.LANGSMITH_API_KEY.optional()
    if not typesafe_key and not gateway_key:
        logger.warning("Jev routing has no API key; using configured default model")
        return "default"
    started_at = time.monotonic()
    try:
        async with httpx2.AsyncClient(timeout=float(ENV.TYPESAFE_TIMEOUT_SECONDS.get())) as client:
            classifier = TypeSafeClassifier(
                model="jev-1.13.0" if typesafe_key else "typesafe/jev-1.13.0",
                api_key=typesafe_key or gateway_key,
                **({} if typesafe_key else {"base_url": gateway_base_url()}),
                async_client=client,
            )
            request: ClassifierRequest = {
                "state": task,
                "questions": {
                    "route": Choice(
                        instructions=prompt("model-selection/instructions"),
                        criteria=_route_criteria(),
                    )
                },
            }
            for attempt in range(2):
                try:
                    response = await classifier.ainvoke(request, config={"tags": ["nostream"]})
                    break
                except TypeSafeAPITimeoutError:
                    if attempt == 1:
                        raise
            answer = response.choices["route"]
        if answer.confidence < 0.6:
            logger.info(
                "Jev routing confidence below threshold; using configured default model",
                extra={"confidence": answer.confidence},
            )
            return "default"
        return RouteDecision.model_validate({"model_route": answer.choice}).model_route
    except TypeSafeAPITimeoutError:
        _ROUTE_FALLBACK.set(True)
        logger.warning(
            "Jev routing timed out; using configured default model",
            extra={"elapsed_seconds": time.monotonic() - started_at},
        )
        return "default"
    except Exception:
        _ROUTE_FALLBACK.set(True)
        logger.exception("Jev routing failed; using configured default model")
        return "default"


class ModelSelectionState(AgentState):
    model_route: NotRequired[PersistedRoute]
    model_route_fallback: NotRequired[bool]


def normalize_route(route: PersistedRoute) -> SelectedRoute:
    return "fast" if route == "fast_alt" else route


async def _emit_routed_model(
    models: Mapping[str, BaseChatModel],
    route_model_ids: Mapping[str, str],
    route: SelectedRoute,
    *,
    fallback: bool = False,
) -> None:
    """Stream the routed model's id so the UI can show it next to `Auto`."""
    model_id = route_model_ids.get(route)
    if model_id is None:
        model = models.get(route)
        model_id = getattr(model, "model_id", None)
    if not isinstance(model_id, str) or not model_id:
        return
    try:
        get_stream_writer()(
            {"type": "model_routed", "route": route, "model_id": model_id, "fallback": fallback}
        )
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
        routing_mode: RoutingMode = "auto",
    ) -> None:
        self._models = {**models, "default": default_model}
        self._route_model_ids = dict(route_model_ids or {})
        self._routing_mode = routing_mode

    async def select_route(
        self,
        state: ModelSelectionState,
    ) -> SelectedRoute:
        """Select the model route for a turn."""
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
    ) -> dict[str, SelectedRoute | bool]:
        del runtime
        route = await self.select_route(state)
        if "model_route_fallback" in state:
            fallback = state["model_route_fallback"]
        elif "model_route" in state:
            fallback = False
        else:
            fallback = _ROUTE_FALLBACK.get()
        if self._routing_mode == "auto":
            await _emit_routed_model(
                self._models,
                self._route_model_ids,
                route,
                fallback=fallback,
            )
        return {"model_route": route, "model_route_fallback": fallback}

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        route: PersistedRoute = request.state.get("model_route", "default")
        model = self._models.get(normalize_route(route)) or self._models["default"]
        return await handler(request.override(model=model))
