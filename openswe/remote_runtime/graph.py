"""One Open SWE graph, played by a run on Managed Deep Agents.

MDA keeps the model loop and its checkpoints. Everything else is the in-process
graph itself, sandbox included, built here for the run exactly as this deployment would
run it: its tools node, with its tool-call middleware around every call, and its
own middleware, which the MDA run hands each phase of its loop to:

- ``prepare`` runs every ``before_agent`` hook: run preparation (the thread's
  sandbox, checkout and prompt), the transcript turn, skills, reply bookkeeping
- ``before_model`` runs every ``before_model`` hook (follow-up queue, event matches,
  proxy refresh, task coordination, model routing), then runs the graph's own
  model-call middleware for the system prompt and the tools it hides from this
  call, and names the model to call
- ``model_result`` lets the transcript and usage middleware observe each response
- ``after_model`` runs ``after_model`` hooks, such as the reply nudges, which can
  send the loop back to the model
- ``after_agent`` runs ``after_agent`` hooks: usage, transcript, step-limit notice,
  review-check settle
- ``call`` runs one tool through the graph's tools node
- ``tool_started`` and ``tool_result`` let the transcript record a tool MDA runs
  itself, such as ``task``, whose subagent runs on MDA

A call made from inside a subagent names the namespace of its ``task`` calls, so
its tool calls and replies land under the subagent, as they do in-process.

The graph's model-call shaping (caching, retries, fallback, summarization, call
limits, provider sanitizers) is MDA's own. Graph state the hooks write that MDA
does not know rides in the MDA run's state and comes back with every call.
"""

import inspect
import logging
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from functools import partial
from typing import Final, Literal, Protocol, cast, get_args, runtime_checkable
from uuid import uuid4

from deepagents.middleware.filesystem import FilesystemMiddleware
from langchain.agents.middleware import ModelCallLimitMiddleware
from langchain.agents.middleware.types import (
    AgentMiddleware,
    AgentState,
    ModelRequest,
    ModelResponse,
)
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import (
    AIMessage,
    AnyMessage,
    BaseMessage,
    ToolCall,
    ToolMessage,
    convert_to_messages,
    messages_from_dict,
    messages_to_dict,
)
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from langchain_quickjs import CodeInterpreterMiddleware
from langgraph.config import get_config
from langgraph.runtime import Runtime
from pydantic import BaseModel, JsonValue, TypeAdapter, ValidationError

from openswe.middleware.conversation_offloading import ConversationOffloadingMiddleware
from openswe.middleware.model_selection import PersistedRoute, normalize_route
from openswe.middleware.record_run_usage import RecordRunUsageMiddleware
from openswe.middleware.transcript import TranscriptMiddleware, within_namespace
from openswe.prompts import common_prompt, prompt
from openswe.remote_runtime.runs import (
    ModelSpec,
    RunModels,
    in_graph_context,
    run_config,
)
from openswe.remote_runtime.server import UnknownHookError
from openswe.remote_runtime.tokens import RemoteRun
from openswe.run_config import RunConfig
from openswe.runtime.constants import DEFAULT_LLM_MAX_TOKENS
from openswe.sandboxes.tool_runtime import (
    EXCLUDED_TOOLS,
    ModelChoice,
    SurfaceModels,
    ToolSurface,
)
from openswe.server import code_only, direct_and_from_code, fallback_model_choice
from openswe.utils.model import provider_model_kwargs

logger = logging.getLogger(__name__)

# MDA's own deep agent runs subagents and code.
_MDA_TOOLS: Final = frozenset({"task", "eval"})
# Tools the model labels each call of, for the dashboard to show in place of a bare name.
_DESCRIBED_TOOLS: Final = ("eval",)
# Model-call middleware that is MDA's own, or that observes rather than shapes a call.
_NOT_PROMPT_SHAPING: Final = (
    FilesystemMiddleware,
    CodeInterpreterMiddleware,
    ConversationOffloadingMiddleware,
    TranscriptMiddleware,
    RecordRunUsageMiddleware,
)
_MODEL_OBSERVERS: Final = (TranscriptMiddleware, RecordRunUsageMiddleware)
# Loop bookkeeping and code mode MDA's own middleware already does.
_MDA_PHASES: Final = (ModelCallLimitMiddleware, CodeInterpreterMiddleware)
# A message inside a state value, as it crosses to MDA and back.
MESSAGE_KEY: Final = "__lc_message__"
_ROUTES: Final = frozenset(get_args(PersistedRoute))
_SESSION_TTL_SECONDS: Final = 6 * 60 * 60
_SESSION_LIMIT: Final = 512

_json = TypeAdapter(JsonValue)
_json_object = TypeAdapter(dict[str, JsonValue])
_json_list = TypeAdapter(list[JsonValue])

Phase = Literal["before_agent", "before_model", "after_model", "after_agent"]
GraphBuilder = Callable[[RunnableConfig, ToolSurface], Awaitable[object]]
StartModels = Callable[[RunConfig], Awaitable[RunModels]]
StateRestorer = Callable[[dict[str, object]], None]


class HookBody(BaseModel):
    """What the MDA run sends with every call: who it is and the state it holds."""

    run_id: str | None = None
    messages: list[dict[str, JsonValue]] = []
    state: dict[str, JsonValue] = {}
    # The ``task`` calls the run's subagent is nested in; empty for the agent itself.
    namespace: list[str] = []


class CallBody(HookBody):
    name: str
    arguments: dict[str, JsonValue] = {}
    tool_call_id: str


class ToolResultBody(HookBody):
    """The result of a tool call MDA ran itself, as a tool message."""

    result: dict[str, JsonValue]


def dump_messages(messages: Sequence[BaseMessage]) -> list[JsonValue]:
    return _json_list.validate_python(messages_to_dict(list(messages)))


def _with_ids(messages: Sequence[BaseMessage]) -> list[BaseMessage]:
    """Messages with ids, so both sides of the run key them the same."""
    for message in messages:
        if not message.id:
            message.id = str(uuid4())
    return list(messages)


def _as_messages(raw: object) -> list[BaseMessage]:
    items = raw if isinstance(raw, list) else [raw]
    return _with_ids(convert_to_messages([item for item in items if item is not None]))


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


def _json_state(update: Mapping[str, object]) -> dict[str, JsonValue]:
    state: dict[str, JsonValue] = {}
    for key, value in update.items():
        try:
            state[key] = _json.validate_python(_encode(value))
        except ValidationError:
            logger.debug("Graph state is not JSON and stays here", extra={"state_key": key})
    return state


def _model_spec(choice: ModelChoice) -> ModelSpec:
    return ModelSpec(
        model_id=choice.model_id, kwargs=_json_object.validate_python(dict(choice.kwargs))
    )


def _overrides(middleware: AgentMiddleware, hook: str) -> bool:
    return getattr(type(middleware), hook) is not getattr(AgentMiddleware, hook)


class ModelCallShape(BaseModel):
    """What the graph's own model-call middleware makes of the next call."""

    system_prompt: str
    # Tools the middleware takes off this call's list, as it would in-process.
    hidden_tools: list[str]


class PhaseResult(BaseModel):
    update: dict[str, JsonValue] = {}
    messages: list[JsonValue] = []
    jump_to: str | None = None
    events: list[JsonValue] = []


@runtime_checkable
class CarriedAcrossProcesses(Protocol):
    """Middleware whose per-run state outlives the process that started the run.

    An MDA run's hooks can reach any backend replica: each replica rejoins the
    run before its first hook, and writes what a hook queued before answering it.
    """

    async def aresume_run(self, state: AgentState, runtime: Runtime) -> None: ...

    async def aflush_run(self, runtime: Runtime) -> None: ...


@runtime_checkable
class RecordsRemoteTools(Protocol):
    """Middleware that records the tool calls MDA runs itself, which never reach the tools node."""

    async def atool_started(self, tool_call: ToolCall, messages: Sequence[BaseMessage]) -> None: ...

    async def atool_finished(self, result: ToolMessage) -> None: ...


class RunSession:
    """One run's in-process graph: its tools node, middleware and models."""

    def __init__(
        self,
        surface: ToolSurface,
        config: RunnableConfig,
        restore: StateRestorer | None,
        *,
        started_here: bool,
    ) -> None:
        self.surface = surface
        self.config = config
        self._restore = restore
        self._joined = started_here
        self.expires_at = time.monotonic() + _SESSION_TTL_SECONDS

    def _carried(self) -> list[CarriedAcrossProcesses]:
        return [m for m in self.surface.middleware if isinstance(m, CarriedAcrossProcesses)]

    async def join(self, state: Mapping[str, object], runtime: Runtime) -> None:
        """Pick up the run where another process left it, once per session."""
        if self._joined:
            return
        self._joined = True
        for middleware in self._carried():
            await middleware.aresume_run(cast(AgentState, dict(state)), runtime)

    async def flush(self, runtime: Runtime) -> None:
        for middleware in self._carried():
            await middleware.aflush_run(runtime)

    def _tool_recorders(self) -> list[RecordsRemoteTools]:
        return [m for m in self.surface.middleware if isinstance(m, RecordsRemoteTools)]

    async def tool_started(self, tool_call: ToolCall, messages: Sequence[BaseMessage]) -> None:
        for middleware in self._tool_recorders():
            await middleware.atool_started(tool_call, messages)

    async def tool_finished(self, result: ToolMessage) -> None:
        for middleware in self._tool_recorders():
            await middleware.atool_finished(result)

    @property
    def models(self) -> SurfaceModels:
        if self.surface.models is None:
            raise RuntimeError("The graph factory picked no models")
        return self.surface.models

    def state(self, body: HookBody) -> dict[str, object]:
        state: dict[str, object] = {
            **{key: decode_state(value) for key, value in body.state.items()},
            "messages": messages_from_dict(body.messages),
        }
        if self._restore is not None:
            self._restore(state)
        return state

    def catalog(self) -> dict[str, JsonValue]:
        tools = {name: tool for name, tool in self.surface.tools.items() if name not in _MDA_TOOLS}
        return _json_object.validate_python(
            {
                "tools": [
                    tool.model_dump(mode="json")
                    for tool in self.surface.catalog()
                    if tool.name in tools
                ],
                "subagents": [
                    {
                        "name": subagent.name,
                        "description": subagent.description,
                        "system_prompt": common_prompt(subagent.system_prompt),
                        "tools": None
                        if subagent.tools is None
                        else [name for name in subagent.tools if name in tools],
                    }
                    for subagent in self.surface.subagents
                ],
                "models": self.start_models().model_dump(),
                # Hidden from the model, as the in-process graph hides them.
                "excluded": sorted((self.surface.excluded | EXCLUDED_TOOLS) - _MDA_TOOLS),
                # Called from MDA's code mode, never offered to the model as schemas.
                "code_only": sorted(
                    name
                    for name in tools
                    if code_only(name)
                    and (self.surface.code_mode or name in self.surface.integration_names)
                ),
                # Offered to the model, and callable from MDA's code mode too.
                "also_from_code": sorted(
                    name for name in tools if self.surface.code_mode and direct_and_from_code(name)
                ),
                "described": {
                    name: prompt("tools/display-description") for name in _DESCRIBED_TOOLS
                },
            }
        )

    def start_models(self) -> RunModels:
        return RunModels(
            model=_model_spec(self.models.main),
            subagent_model=_model_spec(self.models.subagent),
            use_gateway=self.models.use_gateway,
        )

    def _phase_middleware(self, phase: Phase) -> list[AgentMiddleware]:
        hook = f"a{phase}"
        middleware = [
            m
            for m in self.surface.middleware
            if _overrides(m, hook)
            and not isinstance(m, _MDA_PHASES)
            # MDA's own summarization offloads automatically; a requested offload runs here.
            and not (isinstance(m, ConversationOffloadingMiddleware) and not m.manual)
        ]
        # As in a graph run: before hooks in order, after hooks in reverse.
        return middleware if phase.startswith("before") else list(reversed(middleware))

    async def run_phase(
        self, phase: Phase, state: dict[str, object], runtime: Runtime
    ) -> PhaseResult:
        update: dict[str, object] = {}
        added: list[BaseMessage] = []
        jump_to: str | None = None
        for middleware in self._phase_middleware(phase):
            result = await _call_hook(getattr(middleware, f"a{phase}"), state, runtime)
            if not result:
                continue
            values = dict(result)
            target = values.pop("jump_to", None)
            if "messages" in values:
                messages = _as_messages(values.pop("messages"))
                added.extend(messages)
                state["messages"] = [*_as_list(state.get("messages")), *messages]
            update.update(values)
            state.update(values)
            if isinstance(target, str) and target:
                jump_to = target
                break
        return PhaseResult(
            update=_json_state(update), messages=dump_messages(added), jump_to=jump_to
        )

    def _offered(self) -> list[BaseTool]:
        return [tool for name, tool in self.surface.tools.items() if name not in _MDA_TOOLS]

    def _request(
        self,
        state: Mapping[str, object],
        runtime: Runtime,
        tools: Sequence[BaseTool] = (),
    ) -> ModelRequest:
        return ModelRequest(
            model=FakeListChatModel(responses=[""]),
            messages=_as_list(state.get("messages")),
            state=cast(AgentState, dict(state)),
            runtime=runtime,
            tools=list(tools),
            model_settings={},
        )

    async def model_call(self, state: Mapping[str, object], runtime: Runtime) -> ModelCallShape:
        """The system prompt and tool list the graph's own model-call middleware would send."""
        captured: list[ModelRequest] = []

        async def capture(request: ModelRequest) -> ModelResponse:
            captured.append(request)
            return ModelResponse(result=[AIMessage(content="")])

        shaping = [
            m
            for m in self.surface.middleware
            if _overrides(m, "awrap_model_call") and not isinstance(m, _NOT_PROMPT_SHAPING)
        ]
        offered = self._offered()
        await _chain(shaping, capture)(self._request(state, runtime, offered))
        if not captured:
            return ModelCallShape(system_prompt=common_prompt(), hidden_tools=[])
        request = captured[0]
        kept = {getattr(tool, "name", None) for tool in request.tools}
        return ModelCallShape(
            system_prompt=common_prompt(
                request.system_message.text if request.system_message else ""
            ),
            hidden_tools=sorted(tool.name for tool in offered if tool.name not in kept),
        )

    def model_for(self, state: Mapping[str, object]) -> ModelChoice:
        """The model the graph's model selection would call next."""
        requested = state.get("requested_model")
        if isinstance(requested, str) and requested:
            return self._explicit_model(requested, state.get("requested_effort"))
        route = state.get("model_route")
        if route in _ROUTES and route is not None:
            selected_route = normalize_route(cast(PersistedRoute, route))
            routed = self.models.routes.get(selected_route)
            if routed is not None and selected_route != "default":
                return routed
        selected = state.get("selected_model_id")
        if isinstance(selected, str) and selected and selected != self.models.main.model_id:
            return self._explicit_model(selected, state.get("selected_effort"))
        return self.models.main

    def _explicit_model(self, model_id: str, effort: object) -> ModelChoice:
        return ModelChoice(
            model_id,
            dict(
                provider_model_kwargs(
                    model_id,
                    effort if isinstance(effort, str) else None,
                    max_tokens=DEFAULT_LLM_MAX_TOKENS,
                )
            ),
        )

    async def observe(
        self, state: Mapping[str, object], response: list[BaseMessage], runtime: Runtime
    ) -> list[BaseMessage]:
        """Let the transcript and usage middleware see the response MDA's model gave."""

        async def respond(_request: ModelRequest) -> ModelResponse:
            return ModelResponse(result=response)

        observers = [m for m in self.surface.middleware if isinstance(m, _MODEL_OBSERVERS)]
        result = await _chain(observers, respond)(self._request(state, runtime))
        return result.result


async def _call_hook(
    hook: Callable[..., Awaitable[dict[str, object] | None]],
    state: dict[str, object],
    runtime: Runtime,
) -> dict[str, object] | None:
    """Call a middleware hook as a graph run does, with the config when it asks for one."""
    if "config" in inspect.signature(hook).parameters:
        return await hook(cast(AgentState, state), runtime, get_config())
    return await hook(cast(AgentState, state), runtime)


def _as_list(value: object) -> list[AnyMessage]:
    return list(value) if isinstance(value, list) else []


def _chain(
    middleware: Sequence[AgentMiddleware],
    terminal: Callable[[ModelRequest], Awaitable[ModelResponse]],
) -> Callable[[ModelRequest], Awaitable[ModelResponse]]:
    """``middleware`` around ``terminal``, the first outermost, as a graph composes them."""
    handler = terminal
    for m in reversed(middleware):
        handler = partial(_wrap, m, handler)
    return handler


async def _wrap(
    middleware: AgentMiddleware,
    handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    request: ModelRequest,
) -> ModelResponse:
    result = await middleware.awrap_model_call(request, handler)
    if isinstance(result, ModelResponse):
        return result
    if isinstance(result, AIMessage):
        return ModelResponse(result=[result])
    # An ExtendedModelResponse carries the response alongside a state command.
    model_response = getattr(result, "model_response", None)
    if isinstance(model_response, ModelResponse):
        return model_response
    raise TypeError(f"{type(middleware).__name__} returned no model response")


class RemoteGraph:
    """An Open SWE graph's catalog for the MDA runs that play it."""

    def __init__(
        self,
        assistant_id: str,
        build: GraphBuilder,
        start_models: StartModels,
        *,
        restore_state: StateRestorer | None = None,
    ) -> None:
        self.assistant_id = assistant_id
        self._build = build
        self.start_models = start_models
        self._restore_state = restore_state
        self._sessions: dict[tuple[str, str | None], RunSession] = {}

    def _config(self, run: RemoteRun, run_id: str | None) -> RunnableConfig:
        config = run_config(run)
        configurable = dict(config.get("configurable") or {})
        if run_id:
            # The transcript keys a run's turn by it; never the tracer's run id.
            configurable["run_id"] = run_id
        return {**config, "configurable": configurable}

    async def session(
        self, run: RemoteRun, run_id: str | None, *, fresh: bool = False
    ) -> RunSession:
        key = (run.thread_id, run_id)
        cached = self._sessions.get(key)
        if not fresh and cached is not None and time.monotonic() < cached.expires_at:
            return cached
        config = self._config(run, run_id)
        surface = ToolSurface(for_model=True)
        await self._build(config, surface)
        await surface.prepare({})
        session = RunSession(surface, config, self._restore_state, started_here=fresh)
        if len(self._sessions) >= _SESSION_LIMIT:
            now = time.monotonic()
            self._sessions = {k: s for k, s in self._sessions.items() if s.expires_at > now}
        self._sessions[key] = session
        return session

    def end(self, run: RemoteRun, run_id: str | None) -> None:
        self._sessions.pop((run.thread_id, run_id), None)

    async def hook(self, run: RemoteRun, hook: str, raw: Mapping[str, JsonValue]) -> JsonValue:
        """Answer one call the MDA run makes around its model loop."""
        if hook == "call":
            return await self._call(run, CallBody.model_validate(raw))
        body = HookBody.model_validate(raw)
        if hook == "catalog":
            return (await self.session(run, body.run_id)).catalog()
        if hook == "prepare":
            session = await self.session(run, body.run_id, fresh=True)
            return await self._phase(session, "before_agent", body)
        session = await self.session(run, body.run_id)
        if hook == "before_model":
            return await self._phase(session, "before_model", body)
        if hook == "after_model":
            return await self._phase(session, "after_model", body)
        if hook == "after_agent":
            try:
                return await self._phase(session, "after_agent", body)
            finally:
                self.end(run, body.run_id)
        if hook == "model_result":
            return await self._model_result(session, raw, body)
        if hook == "tool_started":
            return await self._tool_started(session, CallBody.model_validate(raw))
        if hook == "tool_result":
            return await self._tool_result(session, ToolResultBody.model_validate(raw))
        raise UnknownHookError(hook)

    async def _phase(self, session: RunSession, phase: Phase, body: HookBody) -> JsonValue:
        state = session.state(body)

        async def run(runtime: Runtime) -> dict[str, JsonValue]:
            await session.join(state, runtime)
            result = await session.run_phase(phase, state, runtime)
            reply = result.model_dump()
            if phase == "before_model" and result.jump_to is None:
                model = session.model_for(state)
                fallback = fallback_model_choice(model.model_id)
                reply.update((await session.model_call(state, runtime)).model_dump())
                reply["model"] = _model_spec(model).model_dump()
                reply["fallback"] = None if fallback is None else _model_spec(fallback).model_dump()
                reply["use_gateway"] = session.models.use_gateway
            await session.flush(runtime)
            return _json_object.validate_python(reply)

        events: list[object] = []
        reply = await in_graph_context(session.config, run, events=events)
        reply["events"] = _json_list.validate_python([_json_or_text(e) for e in events])
        return reply

    async def _model_result(
        self, session: RunSession, raw: Mapping[str, JsonValue], body: HookBody
    ) -> JsonValue:
        response = raw.get("response")
        messages = messages_from_dict(
            [m for m in response if isinstance(m, dict)] if isinstance(response, list) else []
        )
        state = session.state(body)

        async def observe(runtime: Runtime) -> list[BaseMessage]:
            await session.join(state, runtime)
            with within_namespace(body.namespace):
                observed = await session.observe(state, messages, runtime)
            await session.flush(runtime)
            return observed

        observed = await in_graph_context(session.config, observe)
        return {"response": dump_messages(observed)}

    async def _tool_started(self, session: RunSession, body: CallBody) -> JsonValue:
        state = session.state(body)
        tool_call = ToolCall(name=body.name, args=dict(body.arguments), id=body.tool_call_id)

        async def record(runtime: Runtime) -> None:
            await session.join(state, runtime)
            with within_namespace(body.namespace):
                await session.tool_started(tool_call, messages_from_dict(body.messages))
            await session.flush(runtime)

        await in_graph_context(session.config, record)
        return {}

    async def _tool_result(self, session: RunSession, body: ToolResultBody) -> JsonValue:
        state = session.state(body)
        [result] = messages_from_dict([body.result])
        if not isinstance(result, ToolMessage):
            raise ValueError("A tool result must be a tool message")

        async def record(runtime: Runtime) -> None:
            await session.join(state, runtime)
            with within_namespace(body.namespace):
                await session.tool_finished(result)
            await session.flush(runtime)

        await in_graph_context(session.config, record)
        return {}

    async def _call(self, run: RemoteRun, body: CallBody) -> JsonValue:
        session = await self.session(run, body.run_id)
        if body.name in _MDA_TOOLS or body.name not in session.surface.tools:
            return {
                "message": dump_messages(
                    [_error_message(body, f"{body.name} is not an Open SWE tool")]
                )[0],
                "messages": [],
                "update": {},
                "events": [],
            }
        state = session.state(body)
        await in_graph_context(session.config, partial(session.join, state))
        try:
            with within_namespace(body.namespace):
                result = await session.surface.run(
                    session.config,
                    state,
                    body.name,
                    body.arguments,
                    call_id=body.tool_call_id,
                )
        except Exception as exc:
            # The graph's tool-error middleware turns tool failures into results;
            # what is left is a call that never reached a tool, such as bad arguments.
            detail = getattr(exc, "detail", None)
            logger.warning(
                "Remote tool call failed",
                extra={"tool_name": body.name, "thread_id": run.thread_id},
                exc_info=True,
            )
            return {
                "message": dump_messages([_error_message(body, str(detail or exc))])[0],
                "messages": [],
                "update": {},
                "events": [],
            }
        await in_graph_context(session.config, session.flush)
        return _json_object.validate_python(
            {
                "message": dump_messages([result.message])[0],
                "messages": dump_messages(_with_ids(result.messages)),
                "update": _json_state(result.update),
                "events": [_json_or_text(e) for e in result.events],
            }
        )


def _error_message(body: CallBody, text: str) -> BaseMessage:
    return ToolMessage(content=text, tool_call_id=body.tool_call_id, name=body.name, status="error")


def _json_or_text(value: object) -> JsonValue:
    try:
        return _json.validate_python(value)
    except ValidationError:
        return str(value)
