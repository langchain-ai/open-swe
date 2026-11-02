"""A run's graph state as it crosses to MDA and back, and the hooks the backend runs on it.

What readies a run's turn and what records its end are middleware hooks the backend runs
on the state a call carries (``run_hooks``); what they write goes back to MDA as JSON.
"""

import inspect
import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Final, Literal, cast
from uuid import uuid4

from langchain.agents.middleware.types import AgentMiddleware, AgentState
from langchain_core.messages import (
    AnyMessage,
    BaseMessage,
    convert_to_messages,
    messages_from_dict,
    messages_to_dict,
)
from langgraph.config import get_config
from langgraph.runtime import Runtime
from pydantic import JsonValue, TypeAdapter, ValidationError
from runtime_api.loop import PhaseResult, RunCall

logger = logging.getLogger(__name__)

# A message inside a state value, as it crosses to MDA and back.
MESSAGE_KEY: Final = "__lc_message__"

_json = TypeAdapter(JsonValue)
_json_dicts = TypeAdapter(list[dict[str, JsonValue]])

Phase = Literal["before_agent", "after_agent"]
StateRestorer = Callable[[dict[str, object]], None]


def jsonable(value: object) -> JsonValue:
    """``value`` as JSON, or its text where it is not."""
    try:
        return _json.validate_python(value)
    except ValidationError:
        return str(value)


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
    hooked = [m for m in middleware if _overrides(m, hook)]
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
