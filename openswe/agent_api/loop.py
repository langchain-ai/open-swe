"""The graph's middleware around each model call, for runs still served from their plan.

MDA's own middleware runs the loop of every other run (``mda/rt/loop.py``). For a run
still served from its plan, ``RunLoop`` runs the graph's ``before_model`` and
``after_model`` hooks on the state a call carries, folds what its middleware adds
to the call (``ShapesModelCalls``), and names the model to call. The model
settings helpers serve both: ``chosen_model`` answers ``model_choice``.
"""

import inspect
import logging
from collections.abc import Awaitable, Callable, Collection, Mapping, Sequence
from typing import Final, Literal, cast, get_args
from uuid import uuid4

from langchain.agents.middleware import ModelCallLimitMiddleware
from langchain.agents.middleware.types import AgentMiddleware, AgentState
from langchain_core.messages import (
    AnyMessage,
    BaseMessage,
    convert_to_messages,
    messages_from_dict,
    messages_to_dict,
)
from langchain_quickjs import CodeInterpreterMiddleware
from langgraph.config import get_config
from langgraph.runtime import Runtime
from pydantic import JsonValue, TypeAdapter, ValidationError
from runtime_api.catalog import ModelSpec, RunModels
from runtime_api.loop import BeforeModel, ModelCall, PhaseResult, RunCall

from openswe.dashboard.options import model_supports_images
from openswe.middleware.conversation_offloading import ConversationOffloadingMiddleware
from openswe.middleware.model_call_shape import CallShape, ShapesModelCalls
from openswe.middleware.model_selection import PersistedRoute, normalize_route
from openswe.prompts import common_prompt
from openswe.runtime.constants import DEFAULT_LLM_MAX_TOKENS
from openswe.server import fallback_model_choice
from openswe.utils.model import provider_model_kwargs

logger = logging.getLogger(__name__)

# A message inside a state value, as it crosses to MDA and back.
MESSAGE_KEY: Final = "__lc_message__"
# Loop bookkeeping and code mode MDA's own middleware already does.
_MDA_PHASES: Final = (ModelCallLimitMiddleware, CodeInterpreterMiddleware)
# PersistedRoute is a union of literals: its arguments are the literals, not the names.
_ROUTES: Final = frozenset(name for route in get_args(PersistedRoute) for name in get_args(route))

_json = TypeAdapter(JsonValue)
_json_object = TypeAdapter(dict[str, JsonValue])
_json_dicts = TypeAdapter(list[dict[str, JsonValue]])

Phase = Literal["before_agent", "before_model", "after_model", "after_agent"]
StateRestorer = Callable[[dict[str, object]], None]


def dump_messages(messages: Sequence[BaseMessage]) -> list[dict[str, JsonValue]]:
    return _json_dicts.validate_python(messages_to_dict(list(messages)))


def _with_ids(messages: Sequence[BaseMessage]) -> list[BaseMessage]:
    """Messages with ids, so both sides of the run key them the same."""
    for message in messages:
        if not message.id:
            message.id = str(uuid4())
    return list(messages)


def as_messages(raw: object) -> list[BaseMessage]:
    items = raw if isinstance(raw, list) else [raw]
    return _with_ids(convert_to_messages([item for item in items if item is not None]))


def as_list(value: object) -> list[AnyMessage]:
    return list(value) if isinstance(value, list) else []


def _encode(value: object) -> object:
    if isinstance(value, BaseMessage):
        return {MESSAGE_KEY: messages_to_dict([value])[0]}
    if isinstance(value, Mapping):
        return {str(k): _encode(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_encode(v) for v in value]
    return value


def decode_state(value: object) -> object:
    """A state value from MDA, with its messages rebuilt."""
    if isinstance(value, Mapping):
        if set(value) == {MESSAGE_KEY} and isinstance(value[MESSAGE_KEY], dict):
            return messages_from_dict([value[MESSAGE_KEY]])[0]
        return {k: decode_state(v) for k, v in value.items()}
    if isinstance(value, list):
        return [decode_state(v) for v in value]
    return value


def json_state(update: Mapping[str, object]) -> dict[str, JsonValue]:
    state: dict[str, JsonValue] = {}
    for key, value in update.items():
        try:
            state[key] = _json.validate_python(_encode(value))
        except ValidationError:
            logger.debug("Graph state is not JSON and stays here", extra={"state_key": key})
    return state


def call_state(call: RunCall, restore: StateRestorer | None) -> dict[str, object]:
    """The graph state a call from the loop carries, as the graph's hooks read it."""
    state: dict[str, object] = {
        **{key: decode_state(value) for key, value in call.state.items()},
        "messages": messages_from_dict(call.messages),
    }
    if restore is not None:
        restore(state)
    return state


def _overrides(middleware: AgentMiddleware, hook: str) -> bool:
    return getattr(type(middleware), hook) is not getattr(AgentMiddleware, hook)


def phase_middleware(middleware: Sequence[AgentMiddleware], phase: Phase) -> list[AgentMiddleware]:
    """The middleware with a hook for ``phase``, in the order a graph run calls them."""
    hook = f"a{phase}"
    hooked = [
        m
        for m in middleware
        if _overrides(m, hook)
        and not isinstance(m, _MDA_PHASES)
        # MDA's own summarization offloads automatically; a requested offload runs here.
        and not (isinstance(m, ConversationOffloadingMiddleware) and not m.manual)
    ]
    # As in a graph run: before hooks in order, after hooks in reverse.
    return hooked if phase.startswith("before") else list(reversed(hooked))


async def _call_hook(
    hook: Callable[..., Awaitable[dict[str, object] | None]],
    state: dict[str, object],
    runtime: Runtime,
) -> dict[str, object] | None:
    """Call a middleware hook as a graph run does, with the config when it asks for one."""
    if "config" in inspect.signature(hook).parameters:
        return await hook(cast(AgentState, state), runtime, get_config())
    return await hook(cast(AgentState, state), runtime)


async def run_hooks(
    middleware: Sequence[AgentMiddleware], phase: Phase, state: dict[str, object], runtime: Runtime
) -> PhaseResult:
    """Run ``phase``'s hooks on ``state`` as a graph run does, and say what they wrote."""
    update: dict[str, object] = {}
    added: list[BaseMessage] = []
    jump_to: str | None = None
    for hooked in phase_middleware(middleware, phase):
        result = await _call_hook(getattr(hooked, f"a{phase}"), state, runtime)
        if not result:
            continue
        values = dict(result)
        target = values.pop("jump_to", None)
        if "messages" in values:
            messages = as_messages(values.pop("messages"))
            added.extend(messages)
            state["messages"] = [*as_list(state.get("messages")), *messages]
        update.update(values)
        state.update(values)
        if isinstance(target, str) and target:
            jump_to = target
            break
    return PhaseResult(update=json_state(update), messages=dump_messages(added), jump_to=jump_to)


def model_spec(model_id: str, kwargs: Mapping[str, object]) -> ModelSpec:
    """``model_id`` as the runtime calls it: whether it reads images, and its fallback."""
    fallback = fallback_model_choice(model_id)
    return ModelSpec(
        model_id=model_id,
        kwargs=_json_object.validate_python(dict(kwargs)),
        reads_images=model_supports_images(model_id),
        fallback=None
        if fallback is None
        else ModelSpec(
            model_id=fallback.model_id,
            kwargs=_json_object.validate_python(dict(fallback.kwargs)),
            reads_images=model_supports_images(fallback.model_id),
        ),
    )


def chosen_model(model_id: str, effort: object) -> ModelSpec:
    """A model the run calls by id, such as one a person asked for, at ``effort``."""
    kwargs = provider_model_kwargs(
        model_id, effort if isinstance(effort, str) else None, max_tokens=DEFAULT_LLM_MAX_TOKENS
    )
    return model_spec(model_id, kwargs)


class ModelChoices:
    """The model each call goes to: the run's own, a route's, or one the person asked for."""

    def __init__(self, models: RunModels) -> None:
        self.models = models

    def model_for(self, state: Mapping[str, object]) -> ModelSpec:
        """The model the graph's model selection would call next."""
        requested = state.get("requested_model")
        if isinstance(requested, str) and requested:
            return chosen_model(requested, state.get("requested_effort"))
        route = state.get("model_route")
        if route in _ROUTES and route is not None:
            selected_route = normalize_route(cast(PersistedRoute, route))
            routed = self.models.routes.get(selected_route)
            if routed is not None and selected_route != "default":
                return routed
        selected = state.get("selected_model_id")
        if isinstance(selected, str) and selected and selected != self.models.model.model_id:
            return chosen_model(selected, state.get("selected_effort"))
        return self.models.model

    def vision_for(self, model: ModelSpec) -> ModelSpec | None:
        """What a call to ``model`` goes to instead once the conversation holds an image."""
        if self.models.vision is None or model_supports_images(model.model_id):
            return None
        return self.models.vision

    def call(self, shaped: ModelCall, state: Mapping[str, object]) -> ModelCall:
        model = self.model_for(state)
        vision = self.vision_for(model)
        fallback = fallback_model_choice(model.model_id)
        return shaped.model_copy(
            update={
                "model": model,
                "vision": vision,
                "fallback": None
                if fallback is None
                else ModelSpec(
                    model_id=fallback.model_id,
                    kwargs=_json_object.validate_python(dict(fallback.kwargs)),
                ),
                "use_gateway": self.models.use_gateway,
            }
        )


class RunLoop:
    """A graph's middleware around each model call, and the models it picks between.

    ``offered`` names the tools the run offers the model, when known, so the call
    names only those it hides.
    """

    def __init__(
        self,
        middleware: Sequence[AgentMiddleware],
        models: RunModels,
        *,
        offered: Collection[str] | None = None,
    ) -> None:
        self._middleware = list(middleware)
        self._choices = ModelChoices(models)
        self._offered = None if offered is None else frozenset(offered)

    async def model_call(self, state: Mapping[str, object], runtime: Runtime) -> ModelCall:
        """The system prompt and tool list the graph's own middleware gives the next call."""
        call = CallShape()
        for middleware in self._middleware:
            if isinstance(middleware, ShapesModelCalls):
                call = await middleware.ashape_model_call(call, state, runtime)
        hidden = call.hidden_tools if self._offered is None else call.hidden_tools & self._offered
        return ModelCall(
            system_prompt=common_prompt(call.system_prompt), hidden_tools=sorted(hidden)
        )

    async def before_model(self, state: dict[str, object], runtime: Runtime) -> BeforeModel:
        result = await run_hooks(self._middleware, "before_model", state, runtime)
        if result.jump_to is not None:
            return BeforeModel(**result.model_dump())
        call = self._choices.call(await self.model_call(state, runtime), state)
        return BeforeModel(**result.model_dump(), call=call)

    async def after_model(self, state: dict[str, object], runtime: Runtime) -> PhaseResult:
        return await run_hooks(self._middleware, "after_model", state, runtime)
