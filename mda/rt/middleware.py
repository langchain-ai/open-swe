"""The run hooks that hand each phase of this model loop to the Open SWE backend.

The backend holds the in-process graph and its own middleware, and
runs that middleware for every phase here: preparation before the run, the
follow-up queue, routing and the system prompt before each model call, the
transcript and usage after it, reply nudges after the model, and the end-of-run
work. Graph state those hooks write rides in ``open_swe_state`` and goes back
with every call.

Subagents run here too. Their replies and the tools this runtime runs itself,
such as ``task``, go to the backend as well, so its transcript records them
under the subagent, as an in-process run does. Those reports go alongside the
calls; each phase and reply waits for the ones made before it.

A task worker can run here as an async subagent of its coordinator: launched with
``start_async_task``, it is an Open SWE worker thread with its own run token.
"""

import asyncio
import dataclasses
import json
import logging
from collections.abc import (
    Awaitable,
    Callable,
    Collection,
    Iterator,
    Mapping,
    Sequence,
)
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Annotated, Final, NotRequired

import httpx
from deepagents.middleware import async_subagents
from langchain.agents.middleware.types import (
    AgentMiddleware,
    AgentState,
    ModelRequest,
    ModelResponse,
    OmitFromOutput,
    hook_config,
)
from langchain_core.messages import (
    HumanMessage,
    SystemMessage,
    ToolCall,
    ToolMessage,
    messages_from_dict,
)
from langchain_core.tools import BaseTool
from langgraph.config import get_config, get_stream_writer
from langgraph.prebuilt.tool_node import ToolCallRequest, ToolRuntime
from langgraph.runtime import Runtime
from langgraph.types import Command
from langgraph_sdk.errors import APIConnectionError, APIStatusError
from pydantic import BaseModel, JsonValue, TypeAdapter

from rt.backend import (
    BackendCallError,
    Hook,
    OpenSweBackend,
    PhaseReply,
    reporting,
    run_body,
    subagent_of,
)
from rt.models import ModelSpec, build_model
from rt.remote_files import FILE_TOOLS
from rt.tools import STATE_KEY, cited, run_id, tool_body
from rt.tracing import TurnTrace

logger = logging.getLogger(__name__)

_json = TypeAdapter(JsonValue)
_json_object = TypeAdapter(dict[str, JsonValue])
_json_content = TypeAdapter(str | list[JsonValue])

# Graph state MDA's own middleware reads: a requested offload's summarization
# event, which MDA's summarization then applies to every model call.
_MDA_STATE_KEYS: Final = frozenset({"_summarization_event"})
_MESSAGE_KEY: Final = "__lc_message__"
DISPLAY_DESCRIPTION: Final = "display_description"
LAUNCH_WORKER_TOOL: Final = "start_async_task"
# deepagents' tools for the tasks it tracks; Open SWE's task tools track its workers.
_TRACKED_BY_OPEN_SWE: Final = frozenset(
    {"check_async_task", "update_async_task", "cancel_async_task", "list_async_tasks"}
)
WORKER_GRANT_KEY: Final = "__worker_grant"
# Waits before launching again a worker MDA failed to start for a reason that may pass.
_LAUNCH_RETRY_SECONDS: Final = (4.0, 12.0)


def _merge(
    left: dict[str, JsonValue] | None, right: dict[str, JsonValue] | None
) -> dict[str, JsonValue]:
    held = left or {}
    if right is not None and "git_trace_baseline" in right:
        held = {key: value for key, value in held.items() if not key.startswith("git_trace_")}
    return {**held, **(right or {})}


_TOOL_PAIRING_ERRORS: Final = (
    "no tool output found for function call",
    "no tool call found for function call output",
    "unexpected `tool_use_id`",
    "unexpected tool_use_id",
    "without `tool_result`",
    "must be followed by tool messages",
    "must be a response to a preceeding message with 'tool_calls'",
    "must be a response to a preceding message with 'tool_calls'",
)


def _has_image(request: ModelRequest) -> bool:
    return any(
        block.get("type") == "image"
        for message in request.messages
        for block in message.content_blocks
    )


def _tool_pairing_error(exc: Exception) -> bool:
    """A 400 over unpaired tool history, which the fallback would get as well."""
    if getattr(exc, "status_code", None) != 400:
        return False
    message = str(exc).lower()
    return any(marker in message for marker in _TOOL_PAIRING_ERRORS)


class ModelCall(BaseModel):
    """What the backend's middleware says the next model call sends."""

    system_prompt: str
    # Tools the graph's own middleware takes off this call's list.
    hidden_tools: list[str] = []
    model: ModelSpec | None
    # What a call to that model goes to instead when the conversation holds an
    # image: None where the model reads images itself.
    vision: ModelSpec | None = None
    fallback: ModelSpec | None
    use_gateway: bool


class RunState(AgentState):
    open_swe_state: NotRequired[Annotated[dict[str, JsonValue], OmitFromOutput, _merge]]
    open_swe_call: NotRequired[Annotated[dict[str, JsonValue], OmitFromOutput]]
    open_swe_prepared_for: NotRequired[Annotated[str, OmitFromOutput]]


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
    TurnTrace.record_evidence(reply.update)
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

    def __init__(
        self, tools: Sequence[BaseTool], hidden: frozenset[str], backend: OpenSweBackend
    ) -> None:
        self._tools = [tool for tool in tools if tool.name not in hidden]
        self._hidden = hidden
        self._backend = backend

    def _withheld(self) -> frozenset[str]:
        """What a launched worker was declared with from its coordinator's catalog but lacks itself."""
        adopted = self._backend.adopted
        if adopted is None:
            return frozenset()
        own = {spec.name for spec in adopted.operations} | FILE_TOOLS
        return frozenset(adopted.excluded) | {t.name for t in self._tools if t.name not in own}

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        call = request.state.get("open_swe_call")
        hidden = (
            self._hidden
            | self._withheld()
            | (
                frozenset(ModelCall.model_validate(call).hidden_tools)
                if isinstance(call, Mapping)
                else frozenset()
            )
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

    def __init__(
        self,
        backend: OpenSweBackend,
        invocation_id: str | None,
        open_sandbox: Callable[[Runtime], Awaitable[str | None]] | None = None,
        *,
        trace_metadata: Mapping[str, JsonValue] | None = None,
    ) -> None:
        self._backend = backend
        self._invocation_id = invocation_id
        self._trace_metadata = trace_metadata or {}
        self._trace = TurnTrace()
        # Opens the thread's box before preparing, so the backend binds the thread to it.
        self._open_sandbox = open_sandbox

    def _invocation(self) -> str:
        if self._invocation_id is not None:
            return self._invocation_id
        return _RunConfigurable.model_validate(get_config().get("configurable", {})).thread_id

    @hook_config(can_jump_to=["end"])
    async def abefore_agent(self, state: RunState, runtime: Runtime) -> dict[str, object] | None:
        self._trace.start(self._trace_metadata)
        if self._backend.awaiting_adoption:
            # A worker never acts on its grant: it has no identity until it adopts one.
            thread_id = _RunConfigurable.model_validate(get_config().get("configurable", {}))
            assignment = next(
                (m.text for m in state["messages"] if isinstance(m, HumanMessage)), ""
            )
            await self._backend.adopt(thread_id.thread_id, run_id(), assignment)
        invocation = self._invocation()
        if state.get("open_swe_prepared_for") == invocation:
            self._trace.record_evidence(state.get(STATE_KEY, {}))
            return None
        sandbox_id = await self._open_sandbox(runtime) if self._open_sandbox is not None else None
        update = await self._phase("prepare", state, sandbox_id=sandbox_id)
        return {**update, "open_swe_prepared_for": invocation}

    @hook_config(can_jump_to=["end"])
    async def abefore_model(self, state: RunState, runtime: Runtime) -> dict[str, object] | None:  # noqa: ARG002
        return await self._phase("before_model", state)

    async def _phase(self, hook: Hook, state: RunState, **extra: JsonValue) -> dict[str, object]:
        await self._backend.reports.drain()
        # A backend it cannot reach fails the run, as any hook failure does, so every
        # surface reports it and the person can send the message again.
        reply = await self._backend.phase(hook, run_body(run_id(), state, **extra))
        update = _apply(reply)
        if hook == "before_model" and reply.system_prompt is not None:
            update["open_swe_call"] = ModelCall(
                system_prompt=reply.system_prompt,
                hidden_tools=reply.hidden_tools,
                model=reply.model,
                vision=reply.vision,
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
        model = call.vision if call.vision is not None and _has_image(request) else call.model
        if model is not None:
            request = request.override(model=build_model(model, use_gateway=call.use_gateway))
        try:
            return await handler(request)
        except Exception as exc:
            if call.fallback is None or _tool_pairing_error(exc):
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
        await self._backend.reports.drain()
        metadata = dict(self._trace_metadata)
        try:
            body = run_body(run_id(), state)
            held = body.get("state")
            body["state"] = {**(held if isinstance(held, dict) else {}), **self._trace.evidence}
            reply = await self._backend.phase("after_agent", body)
            metadata.update(reply.trace_metadata)
            return _apply(reply)
        except BackendCallError:
            logger.warning("The backend's end-of-run hooks failed", exc_info=True)
            return None
        finally:
            await self._trace.reconcile(metadata)


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
        # The transcript records the calls this reply follows before the reply itself.
        await self._backend.reports.drain()
        observed = await self._backend.model_result(
            run_body(run_id(), request.state), response.result
        )
        if not observed:
            return response
        return ModelResponse(
            result=messages_from_dict(observed),
            structured_response=response.structured_response,
        )


async def reported(
    backend: OpenSweBackend,
    request: ToolCallRequest,
    handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
) -> ToolMessage | Command:
    """Run ``request``, telling the backend's transcript it started and how it ended.

    The reports go out alongside the call rather than before it. A ``task`` call's
    subagent runs here, so every call made within it names the ``task`` call.
    """
    tool_call_id = request.tool_call.get("id")
    if not tool_call_id:
        return await handler(request)
    body = tool_body(
        request.state,
        name=request.tool_call["name"],
        arguments=request.tool_call["args"],
        tool_call_id=tool_call_id,
    )
    backend.reports.started(body)
    try:
        with subagent_of(tool_call_id), reporting(tool_call_id):
            result = await handler(request)
    except asyncio.CancelledError:
        # A stopped call stays open, as it does in-process.
        raise
    except Exception as exc:
        failure = ToolMessage(
            content=f"{type(exc).__name__}: {exc}", tool_call_id=tool_call_id, status="error"
        )
        backend.reports.finished(body, failure)
        raise
    backend.reports.finished(body, _tool_message(result, tool_call_id))
    return result


class RuntimeToolsMiddleware(AgentMiddleware):
    """Report every tool call this runtime makes to the backend's transcript.

    ``FileToolsMiddleware`` reports the file and shell calls itself, as ``reported``
    is around the checks it applies; ``reported_elsewhere`` names them.
    """

    def __init__(self, backend: OpenSweBackend, reported_elsewhere: Collection[str]) -> None:
        self._backend = backend
        self._served = frozenset(reported_elsewhere)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        if request.tool_call["name"] in self._served:
            return await handler(request)
        return await reported(self._backend, request, handler)


class IncidentEvidenceMiddleware(AgentMiddleware):
    """Record each tool result of an incident run as evidence, which the result then cites.

    The report the run writes cites what it found by evidence id, so each result
    carries the id the backend recorded it under.
    """

    def __init__(self, backend: OpenSweBackend) -> None:
        self._backend = backend

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        result = await handler(request)
        if isinstance(result, ToolMessage):
            return await self._cited(request, result)
        if isinstance(result, Command) and isinstance(result.update, dict):
            messages = result.update.get("messages")
            if isinstance(messages, list):
                cited = [
                    await self._cited(request, m) if isinstance(m, ToolMessage) else m
                    for m in messages
                ]
                return dataclasses.replace(result, update={**result.update, "messages": cited})
        return result

    async def _cited(self, request: ToolCallRequest, result: ToolMessage) -> ToolMessage:
        return await cited(
            self._backend, request.tool_call["name"], request.tool_call["args"], result
        )


class _Launched(BaseModel):
    thread_id: str
    run_id: str


class _LaunchErrors(logging.Filter):
    """What ``start_async_task`` failed with: deepagents logs the exception, then answers with text."""

    def __init__(self) -> None:
        super().__init__()
        self._caught: ContextVar[list[BaseException] | None] = ContextVar(
            "open_swe_launch_errors", default=None
        )

    def filter(self, record: logging.LogRecord) -> bool:
        caught = self._caught.get()
        if caught is not None and isinstance(record.args, tuple):
            caught.extend(arg for arg in record.args if isinstance(arg, BaseException))
        return True

    @contextmanager
    def catching(self) -> Iterator[list[BaseException]]:
        caught: list[BaseException] = []
        token = self._caught.set(caught)
        try:
            yield caught
        finally:
            self._caught.reset(token)


_LAUNCH_ERRORS: Final = _LaunchErrors()
logging.getLogger(async_subagents.__name__).addFilter(_LAUNCH_ERRORS)


def _transient(error: BaseException) -> bool:
    """Whether MDA may start the worker if asked again: it did not answer, or was overloaded or failing."""
    if isinstance(error, APIStatusError):
        return error.status_code == 429 or error.status_code >= 500
    return isinstance(
        error, httpx.TransportError | APIConnectionError | TimeoutError | ConnectionError
    )


class AsyncWorkerLaunchMiddleware(AgentMiddleware):
    """Launch task workers with deepagents' ``start_async_task``, each an Open SWE worker.

    The launch would hand the worker this run's context, run token included, so the
    worker would act as this run; and deepagents would wake this agent with the
    worker's context when the worker ends. Instead the worker gets a worker grant to
    adopt a run token of its own, and no assistant of this agent's to wake. Open SWE
    registers it under this agent's task and returns its results as task messages,
    so deepagents does not track the task here, and its own task tools stay unused.

    A launch MDA failed to start for a reason that may pass is made again, for the
    same tool call, with a grant for that same call: a worker an earlier attempt
    started after all is returned, and of two such threads only the first to register
    becomes the worker, the other refused and stopped.
    """

    def __init__(self, backend: OpenSweBackend) -> None:
        self._backend = backend

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        call = request.tool_call
        tool_call_id = call.get("id") or ""
        if call["name"] in _TRACKED_BY_OPEN_SWE:
            return _refused(
                call["name"],
                tool_call_id,
                "Open SWE tracks its workers itself: use task_status, message_task_thread "
                "or control_worker instead.",
            )
        if call["name"] != LAUNCH_WORKER_TOOL:
            return await handler(request)
        description = call["args"].get("description")
        instructions = description if isinstance(description, str) else ""
        body: dict[str, JsonValue] = {"tool_call_id": tool_call_id, "instructions": instructions}
        try:
            return await self._launch(request, handler, body)
        except BackendCallError as exc:
            return _refused(call["name"], tool_call_id, str(exc))

    async def _launch(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
        body: dict[str, JsonValue],
    ) -> ToolMessage:
        call = request.tool_call
        tool_call_id = call.get("id") or ""
        waits = iter(_LAUNCH_RETRY_SECONDS)
        while True:
            reply = await self._backend.call("launch_worker", body)
            if (worker := reply.get("worker")) is not None:
                # A replay of a launch that already made its worker.
                return _worker_message(worker, tool_call_id)
            grant, assignment = reply.get("grant"), reply.get("assignment")
            if not isinstance(grant, str) or not isinstance(assignment, str):
                raise BackendCallError("launch_worker returned no grant")
            # The worker is told its assignment as Open SWE words it, not the bare description.
            launch = ToolCall(
                name=call["name"],
                args={**call["args"], "description": assignment},
                id=call.get("id"),
            )
            with _LAUNCH_ERRORS.catching() as errors:
                result = await handler(
                    request.override(
                        tool_call=launch, runtime=_launch_runtime(request.runtime, grant)
                    )
                )
            if (launched := _launched_task(result)) is not None:
                return await self._register(body, launched, tool_call_id)
            if any(map(_transient, errors)) and (wait := next(waits, None)) is not None:
                logger.warning(
                    "MDA did not start a task worker; launching it again",
                    extra={"tool_call_id": tool_call_id, "wait_seconds": wait},
                    exc_info=errors[-1],
                )
                await asyncio.sleep(wait)
                continue
            return _refused(call["name"], tool_call_id, _not_launched(result, tool_call_id))

    async def _register(
        self, body: dict[str, JsonValue], launched: _Launched, tool_call_id: str
    ) -> ToolMessage:
        try:
            registered = await self._backend.call(
                "worker_launched",
                {**body, "worker_thread_id": launched.thread_id, "run_id": launched.run_id},
            )
        except BackendCallError:
            # The worker is running and registers itself when it adopts its run token;
            # reporting a failed launch would only start a second worker.
            logger.warning("The backend did not register a launched worker", exc_info=True)
            return _worker_message({"worker_thread_id": launched.thread_id}, tool_call_id)
        return _worker_message(registered.get("worker"), tool_call_id)


def _launch_runtime(runtime: ToolRuntime, grant: str) -> ToolRuntime:
    """The tool runtime the launch sees: a worker grant for context, in place of the run's."""
    return dataclasses.replace(runtime, context={"graph": "agent", WORKER_GRANT_KEY: grant})


def _launched_task(result: ToolMessage | Command) -> _Launched | None:
    """The thread and run ``start_async_task`` launched, from the task it would track."""
    update = result.update if isinstance(result, Command) else None
    tasks = update.get("async_tasks") if isinstance(update, Mapping) else None
    if not isinstance(tasks, Mapping) or len(tasks) != 1:
        return None
    [task] = tasks.values()
    return _Launched.model_validate(task)


def _not_launched(result: ToolMessage | Command, tool_call_id: str) -> str:
    cause = _tool_message(result, tool_call_id).text.strip() or "MDA started no worker."
    return (
        f"The worker did not launch. {cause} Calling start_async_task again is safe: "
        "one launch never starts more than one worker."
    )


def _worker_message(worker: object, tool_call_id: str) -> ToolMessage:
    return ToolMessage(
        content=json.dumps(worker), tool_call_id=tool_call_id, name=LAUNCH_WORKER_TOOL
    )


def _refused(name: str, tool_call_id: str, reason: str) -> ToolMessage:
    return ToolMessage(
        content=f"Error: {reason}", tool_call_id=tool_call_id, name=name, status="error"
    )


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
