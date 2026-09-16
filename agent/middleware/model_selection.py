import hashlib
import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any, Literal, NotRequired

from langchain.agents.middleware.types import AgentState, ModelRequest, ModelResponse
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, ToolMessage
from langgraph.config import get_config, get_stream_writer
from langgraph.runtime import Runtime
from pydantic import BaseModel

from agent.input_messages import input_message_text, message_sender_id
from agent.middleware.trace import OpenSWEMiddleware
from agent.prompts import load_prompt, render_prompt

logger = logging.getLogger(__name__)

Route = Literal["fast", "fast_alt", "balanced", "performance"]
RoutingMode = Literal["auto", "performant"]

# A/B experiment: "fast" sends a share of fast-routed turns to a second model
# (``fast_alt``) so the two can be compared under real traffic. The share is
# drawn from a hash of the thread id, so a thread always lands on the same side
# and the split is fully repeatable.
_FAST_ALT_SPLIT = 0.5

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


def fast_alt_bucket(thread_id: str | None) -> float:
    """Deterministic [0, 1) bucket for a thread, from the first 8 hex digits of SHA-256."""
    digest = hashlib.sha256((thread_id or "").encode("utf-8")).hexdigest()
    return int(digest[:8], 16) / float(0xFFFF_FFFF)


def _mark_model_routing_applied() -> None:
    """Mark the current invocation as model-routed."""
    try:
        config = get_config()
        metadata = config.setdefault("metadata", {})
        metadata["model_routing_applied"] = True
        from langsmith.run_helpers import get_current_run_tree

        run_tree = get_current_run_tree()
        if run_tree is not None:
            run_tree.metadata["model_routing_applied"] = True
    except Exception:  # noqa: BLE001
        logger.debug("Could not mark model routing metadata", exc_info=True)


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
        fast_alt_probability: float = _FAST_ALT_SPLIT,
        routing_mode: RoutingMode = "auto",
        thread_id: str | None = None,
    ) -> None:
        self._models = dict(models)
        self._route_model_ids = dict(route_model_ids or {})
        self._fast_alt_probability = fast_alt_probability
        self._routing_mode = routing_mode
        self._selected_route: Route | None = None
        self._thread_id = thread_id
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
            return model_route
        if self._selected_route is not None:
            return self._selected_route
        if self._routing_mode == "performant":
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
        if (
            route == "fast"
            and "fast_alt" in self._models
            and fast_alt_bucket(self._thread_id) < self._fast_alt_probability
        ):
            route = "fast_alt"
        self._selected_route = route
        return route

    async def abefore_model(
        self,
        state: ModelSelectionState,
        runtime: Runtime,
    ) -> dict[str, Route]:
        del runtime
        route = await self.select_route(state)
        if (
            self._routing_mode == "auto"
            and not state.get("model_route")
            and not state.get("plan_mode")
        ):
            _mark_model_routing_applied()
            await _emit_routed_model(self._models, self._route_model_ids, route)
        if state.get("plan_mode"):
            return {}
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
        model = self._models.get(route) or self._models.get(
            "fast" if route == "fast_alt" else "balanced"
        )
        if model is None:
            model = self._models["balanced"]
        return await handler(request.override(model=model))
