"""The run hooks that hand each phase of this model loop to the Open SWE backend.

The backend holds the in-process graph, its sandbox and its own middleware, and
runs that middleware for every phase here: preparation before the run, the
follow-up queue, routing and the system prompt before each model call, the
transcript and usage after it, reply nudges after the model, and the end-of-run
work. Graph state those hooks write rides in ``open_swe_state`` and goes back
with every call.

Subagents run here too. Their replies and the tools this runtime runs itself,
such as ``task``, go to the backend as well, so its transcript records them
under the subagent, as an in-process run does.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Collection, Coroutine, Mapping, Sequence
from typing import Annotated, Final, NotRequired

from langchain.agents.middleware.types import (
    AgentMiddleware,
    AgentState,
    ModelRequest,
    ModelResponse,
    OmitFromOutput,
    hook_config,
)
from langchain_core.messages import SystemMessage, ToolMessage, messages_from_dict
from langchain_core.tools import BaseTool
from langgraph.config import get_config, get_stream_writer
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.runtime import Runtime
from langgraph.types import Command
from pydantic import BaseModel, JsonValue

from rt.backend import (
    BackendCallError,
    BackendUnavailableError,
    Hook,
    OpenSweBackend,
    PhaseReply,
    run_body,
    subagent_of,
)
from rt.models import ModelSpec, build_model
from rt.tools import STATE_KEY, run_id

logger = logging.getLogger(__name__)

# Graph state MDA's own middleware reads: a requested offload's summarization
# event, which MDA's summarization then applies to every model call.
_MDA_STATE_KEYS: Final = frozenset({"_summarization_event"})
_MESSAGE_KEY: Final = "__lc_message__"
DISPLAY_DESCRIPTION: Final = "display_description"


def _merge(
    left: dict[str, JsonValue] | None, right: dict[str, JsonValue] | None
) -> dict[str, JsonValue]:
    return {**(left or {}), **(right or {})}


class ModelCall(BaseModel):
    """What the backend's middleware says the next model call sends."""

    system_prompt: str
    # Tools the graph's own middleware takes off this call's list.
    hidden_tools: list[str] = []
    model: ModelSpec | None
    fallback: ModelSpec | None
    use_gateway: bool


class RunState(AgentState):
    open_swe_state: NotRequired[Annotated[dict[str, JsonValue], OmitFromOutput, _merge]]
    open_swe_call: NotRequired[Annotated[dict[str, JsonValue], OmitFromOutput]]
    open_swe_prepared_for: NotRequired[Annotated[str, OmitFromOutput]]
    open_swe_backend_failure: NotRequired[dict[str, JsonValue] | None]


class _RunConfigurable(BaseModel):
    thread_id: str


def _decode(value: object) -> object:
    """A state value from the backend, with its messages rebuilt."""
    if isinstance(value, Mapping):
        if set(value) == {_MESSAGE_KEY} and isinstance(value[_MESSAGE_KEY], dict):
            return messages_from_dict([value[_MESSAGE_KEY]])[0]
        return {k: _decode(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_decode(v) for v in value]
    return value


def _apply(reply: PhaseReply) -> dict[str, object]:
    for event in reply.events:
        get_stream_writer()(event)
    update: dict[str, object] = {}
    held = {k: v for k, v in reply.update.items() if k not in _MDA_STATE_KEYS}
    if held:
        update[STATE_KEY] = held
    for key in _MDA_STATE_KEYS & reply.update.keys():
        update[key] = _decode(reply.update[key])
    if reply.messages:
        update["messages"] = messages_from_dict(reply.messages)
    if reply.jump_to:
        update["jump_to"] = reply.jump_to
    return update


class ServedToolsMiddleware(AgentMiddleware):
    """Offer the model the graph's tools as the graph does: all of its own, none it hides.

    The backend's tools act on its sandbox, so they stay offered even where MDA's
    filesystem middleware drops one it cannot back itself, such as ``execute``.
    Hidden tools stay registered, so a call by name still runs, as in-process; the
    backend names the ones its middleware hides from each call.
    """

    def __init__(self, tools: Sequence[BaseTool], hidden: frozenset[str]) -> None:
        self._tools = [tool for tool in tools if tool.name not in hidden]
        self._hidden = hidden

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        call = request.state.get("open_swe_call")
        hidden = self._hidden | (
            frozenset(ModelCall.model_validate(call).hidden_tools)
            if isinstance(call, Mapping)
            else frozenset()
        )
        offered = [t for t in request.tools if getattr(t, "name", None) not in hidden]
        names = {getattr(tool, "name", None) for tool in offered}
        missing = [tool for tool in self._tools if tool.name not in names | hidden]
        return await handler(request.override(tools=[*offered, *missing]))


class DescribedToolsMiddleware(AgentMiddleware):
    """Have the model label its calls of the named tools, for the dashboard to show.

    Each named tool the model is offered takes a required ``display_description``,
    first so the model writes it before the rest. The tool call keeps it, so the
    transcript records it, and the tool runs without it. Place it inside code mode,
    which binds the request's tools to ``eval`` before this rewrites them.
    """

    def __init__(self, described: Mapping[str, str]) -> None:
        self._described = described
        self._offered: dict[int, tuple[BaseTool, BaseTool]] = {}

    def _offered_as(self, tool: BaseTool) -> BaseTool:
        cached = self._offered.get(id(tool))
        if cached is not None and cached[0] is tool:
            return cached[1]
        schema = tool.tool_call_schema
        parameters = (
            schema
            if isinstance(schema, dict)
            else schema.model_json_schema()
            if issubclass(schema, BaseModel)
            else schema.schema()
        )
        properties = parameters.get("properties")
        if isinstance(properties, dict) and DISPLAY_DESCRIPTION in properties:
            self._offered[id(tool)] = (tool, tool)
            return tool
        required = parameters.get("required")
        offered = tool.model_copy(
            update={
                "args_schema": {
                    **parameters,
                    "type": "object",
                    "properties": {
                        DISPLAY_DESCRIPTION: {
                            "type": "string",
                            "description": self._described[tool.name],
                        },
                        **(properties if isinstance(properties, dict) else {}),
                    },
                    "required": [
                        DISPLAY_DESCRIPTION,
                        *(required if isinstance(required, list) else []),
                    ],
                }
            }
        )
        self._offered[id(tool)] = (tool, offered)
        return offered

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        tools = [
            self._offered_as(tool)
            if isinstance(tool, BaseTool) and tool.name in self._described
            else tool
            for tool in request.tools
        ]
        return await handler(request.override(tools=tools))

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        call = request.tool_call
        if (
            call["name"] not in self._described
            or DISPLAY_DESCRIPTION not in call["args"]
            or (request.tool is not None and self._offered_as(request.tool) is request.tool)
        ):
            return await handler(request)
        args = {key: value for key, value in call["args"].items() if key != DISPLAY_DESCRIPTION}
        return await handler(request.override(tool_call={**call, "args": args}))


class BackendRunMiddleware(AgentMiddleware[RunState]):
    """Run the Open SWE graph's own middleware on the backend, phase by phase."""

    state_schema = RunState

    def __init__(self, backend: OpenSweBackend, invocation_id: str | None) -> None:
        self._backend = backend
        self._invocation_id = invocation_id

    def _invocation(self) -> str:
        if self._invocation_id is not None:
            return self._invocation_id
        return _RunConfigurable.model_validate(get_config().get("configurable", {})).thread_id

    @hook_config(can_jump_to=["end"])
    async def abefore_agent(self, state: RunState, runtime: Runtime) -> dict[str, object] | None:  # noqa: ARG002
        invocation = self._invocation()
        if state.get("open_swe_prepared_for") == invocation:
            return {"open_swe_backend_failure": None}
        update = await self._phase("prepare", state)
        if not update.get("open_swe_backend_failure"):
            update.update(open_swe_prepared_for=invocation, open_swe_backend_failure=None)
        return update

    @hook_config(can_jump_to=["end"])
    async def abefore_model(self, state: RunState, runtime: Runtime) -> dict[str, object] | None:  # noqa: ARG002
        return await self._phase("before_model", state)

    async def _phase(self, hook: Hook, state: RunState) -> dict[str, object]:
        try:
            reply = await self._backend.phase(hook, run_body(run_id(), state))
        except BackendUnavailableError:
            logger.warning(
                "The backend interrupted a run phase", extra={"hook": hook}, exc_info=True
            )
            return {
                "open_swe_backend_failure": {"run_id": run_id(), "phase": hook},
                "jump_to": "end",
            }
        update = _apply(reply)
        if hook == "before_model" and reply.system_prompt is not None:
            update["open_swe_call"] = ModelCall(
                system_prompt=reply.system_prompt,
                hidden_tools=reply.hidden_tools,
                model=reply.model,
                fallback=reply.fallback,
                use_gateway=reply.use_gateway,
            ).model_dump()
        return update

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        raw = request.state.get("open_swe_call")
        if not isinstance(raw, Mapping):
            return await handler(request)
        call = ModelCall.model_validate(raw)
        existing = request.system_message.text if request.system_message is not None else ""
        prompt = f"{call.system_prompt}\n\n{existing}" if existing else call.system_prompt
        request = request.override(system_message=SystemMessage(content=prompt))
        if call.model is not None:
            request = request.override(model=build_model(call.model, use_gateway=call.use_gateway))
        try:
            return await handler(request)
        except Exception:
            if call.fallback is None:
                raise
            # As the in-process graph does: another provider before failing the run.
            logger.warning("Model call failed; retrying on the fallback model", exc_info=True)
            return await handler(
                request.override(model=build_model(call.fallback, use_gateway=call.use_gateway))
            )

    @hook_config(can_jump_to=["model", "end"])
    async def aafter_model(self, state: RunState, runtime: Runtime) -> dict[str, object] | None:  # noqa: ARG002
        return await self._phase("after_model", state)

    async def aafter_agent(self, state: RunState, runtime: Runtime) -> dict[str, object] | None:  # noqa: ARG002
        failure = state.get("open_swe_backend_failure")
        if failure and failure.get("run_id") == run_id():
            return None
        try:
            return _apply(await self._backend.phase("after_agent", run_body(run_id(), state)))
        except BackendCallError:
            # The backend's completion webhook settles what this run left open.
            logger.warning("The backend's end-of-run hooks failed", exc_info=True)
            return None


class ReportRepliesMiddleware(AgentMiddleware):
    """Hand each reply to the backend, where the graph's transcript and usage middleware see it."""

    def __init__(self, backend: OpenSweBackend) -> None:
        self._backend = backend

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        response = await handler(request)
        observed = await self._backend.model_result(
            run_body(run_id(), request.state), response.result
        )
        if not observed:
            return response
        return ModelResponse(
            result=messages_from_dict(observed),
            structured_response=response.structured_response,
        )


class RuntimeToolsMiddleware(AgentMiddleware):
    """Report the tool calls this runtime runs itself, such as ``task``, to the backend's transcript.

    The backend records the calls of its own tools as it runs them. A ``task``
    call's subagent runs here, so every call it makes names the ``task`` call.
    Reporting is best effort: a call the backend could not record still runs.
    """

    def __init__(self, backend: OpenSweBackend, served: Collection[str]) -> None:
        self._backend = backend
        self._served = frozenset(served)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        name = request.tool_call["name"]
        tool_call_id = request.tool_call.get("id")
        if name in self._served or not tool_call_id:
            return await handler(request)
        body = run_body(
            run_id(),
            request.state,
            name=name,
            arguments=request.tool_call["args"],
            tool_call_id=tool_call_id,
        )
        await self._report(self._backend.tool_started(body))
        try:
            with subagent_of(tool_call_id):
                result = await handler(request)
        except asyncio.CancelledError:
            # A stopped call stays open, as it does in-process.
            raise
        except Exception as exc:
            failure = ToolMessage(
                content=f"{type(exc).__name__}: {exc}", tool_call_id=tool_call_id, status="error"
            )
            await self._report(self._backend.tool_result(body, failure))
            raise
        await self._report(self._backend.tool_result(body, _tool_message(result, tool_call_id)))
        return result

    @staticmethod
    async def _report(call: Coroutine[object, object, None]) -> None:
        try:
            await call
        except BackendCallError:
            logger.warning("The backend did not record a runtime tool call", exc_info=True)


def _tool_message(result: ToolMessage | Command, tool_call_id: str) -> ToolMessage:
    """The message a call answered with; a ``task`` call answers with a command carrying it."""
    if isinstance(result, ToolMessage):
        return result
    update = result.update
    messages = update.get("messages") if isinstance(update, Mapping) else None
    for message in messages if isinstance(messages, Sequence) else ():
        if isinstance(message, ToolMessage) and message.tool_call_id == tool_call_id:
            return message
    return ToolMessage(content="", tool_call_id=tool_call_id)
