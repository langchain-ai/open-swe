"""The run's middleware around Open SWE's model loop.

Preparation is the backend's: it readies the run and writes the graph state the
loop reads, which rides in ``open_swe_state`` and goes back with every call. Around
each model call ``RunLoop`` (``rt.loop``) does what the catalog's rules say, and the
transcript and usage hear of each reply. A run whose catalog names no rules hands
each phase to the backend instead.

Before each model call the run also takes its thread's inbox: what was sent to
the thread while the run worked, such as a person's follow-up or a task report.

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
from typing import Annotated, Final, NotRequired, cast

import httpx
from deepagents.middleware import async_subagents
from langchain.agents.middleware.types import (
    AgentMiddleware,
    AgentState,
    ExtendedModelResponse,
    ModelRequest,
    ModelResponse,
    OmitFromOutput,
    hook_config,
)
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolCall,
    ToolMessage,
    messages_from_dict,
    messages_to_dict,
)
from langchain_core.tools import BaseTool
from langgraph.config import get_config, get_stream_writer
from langgraph.graph.message import add_messages
from langgraph.prebuilt.tool_node import ToolCallRequest, ToolRuntime
from langgraph.runtime import Runtime
from langgraph.types import Command
from langgraph_sdk.errors import APIConnectionError, APIStatusError
from pydantic import BaseModel, JsonValue, TypeAdapter
from runtime_api.catalog import InboxSpec
from runtime_api.endpoint import Endpoint, call
from runtime_api.loop import (
    AFTER_AGENT,
    AFTER_MODEL,
    BEFORE_MODEL,
    INBOX,
    PREPARE,
    Inbox,
    InboxRead,
    InboxReceipt,
    ModelCall,
    ModelResult,
    PhaseResult,
    Prepare,
    RunCall,
)
from runtime_api.workers import LAUNCH_WORKER, WORKER_LAUNCHED, Launch, Launched

from rt.backend import (
    BackendCallError,
    OpenSweBackend,
    RunLocal,
    human_messages,
    namespace,
    reporting,
    run_call,
    subagent_of,
)
from rt.loop import RunLoop
from rt.managed_sandbox import OpenedSandbox
from rt.models import build_model
from rt.outbox import OUTBOX_KEY, carry
from rt.tools import STATE_KEY, cited, run_id, tool_report
from rt.tracing import MIDDLEWARE_TRACE_POLICY, TurnTrace

logger = logging.getLogger(__name__)

_json = TypeAdapter(JsonValue)
_json_object = TypeAdapter(dict[str, JsonValue])
_json_dicts = TypeAdapter(list[dict[str, JsonValue]])
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
# The catalog a run's graph is built from, planned by the backend as the run starts.
CATALOG_KEY: Final = "__catalog"
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


class RunState(AgentState):
    open_swe_state: NotRequired[Annotated[dict[str, JsonValue], OmitFromOutput, _merge]]
    open_swe_outbox: NotRequired[Annotated[dict[str, JsonValue], OmitFromOutput, carry]]
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


def _apply(reply: PhaseResult) -> dict[str, object]:
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


class DescribedToolsMiddleware(AgentMiddleware):
    """Have the model label its calls of the named tools, for the dashboard to show.

    Each named tool the model is offered takes a required ``display_description``,
    first so the model writes it before the rest. The tool call keeps it, so the
    transcript records it, and the tool runs without it. Place it inside code mode,
    which binds the request's tools to ``eval`` before this rewrites them.
    """

    trace_policy = MIDDLEWARE_TRACE_POLICY

    def __init__(self, backend: OpenSweBackend) -> None:
        self._backend = backend
        self._offered = RunLocal[dict[tuple[int, str], tuple[BaseTool, BaseTool]]](dict)

    @property
    def _described(self) -> Mapping[str, str]:
        return self._backend.run.catalog.described

    def _offered_as(self, tool: BaseTool) -> BaseTool:
        label = self._described[tool.name]
        offered_cache = self._offered.get(self._backend.run)
        cached = offered_cache.get((id(tool), label))
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
            offered_cache[(id(tool), label)] = (tool, tool)
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
                            "description": label,
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
        offered_cache[(id(tool), label)] = (tool, offered)
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
        tool_call = request.tool_call
        if (
            tool_call["name"] not in self._described
            or DISPLAY_DESCRIPTION not in tool_call["args"]
            or (request.tool is not None and self._offered_as(request.tool) is request.tool)
        ):
            return await handler(request)
        args = {k: v for k, v in tool_call["args"].items() if k != DISPLAY_DESCRIPTION}
        return await handler(request.override(tool_call={**tool_call, "args": args}))


class BackendRunMiddleware(AgentMiddleware[RunState]):
    """Prepare the run on the backend, read its inbox, and run its loop around each model call."""

    trace_policy = MIDDLEWARE_TRACE_POLICY

    state_schema = RunState

    def __init__(
        self,
        backend: OpenSweBackend,
        open_sandbox: Callable[[Runtime], Awaitable[OpenedSandbox]] | None = None,
    ) -> None:
        self._backend = backend
        self._run_loop = RunLoop(backend)
        # Each run's own model, built from its catalog.
        self._models = RunLocal(self._build_model)
        self._traces = RunLocal(TurnTrace)
        # Opens the thread's box before preparing, so the backend binds the thread to it.
        self._open_sandbox = open_sandbox

    @property
    def _inbox(self) -> InboxSpec | None:
        """How the catalog says to read the thread's inbox; None reads nothing."""
        return self._backend.run.catalog.inbox

    @property
    def _loop(self) -> RunLoop | None:
        """What happens around each model call; None hands each phase to the backend, for
        the runs it still answers from their plan."""
        return self._run_loop if RunLoop.runs(self._backend.run.catalog) else None

    def _invocation(self) -> str:
        if (invocation_id := self._backend.invocation_id) is not None:
            return invocation_id
        return _RunConfigurable.model_validate(get_config().get("configurable", {})).thread_id

    def _own_model(self) -> BaseChatModel | None:
        run = self._backend.run
        models = run.catalog.models
        if models is None:
            return None
        return self._models.get(run)

    def _build_model(self) -> BaseChatModel:
        models = self._backend.run.catalog.models
        if models is None:
            raise BackendCallError("The run has no model configuration")
        return build_model(models.model, use_gateway=models.use_gateway)

    def _trace_metadata(self) -> dict[str, JsonValue]:
        """Who the run acts for."""
        return {**self._backend.run.catalog.trace_metadata}

    def _carrying(
        self, state: Mapping[str, object], update: dict[str, object] | None
    ) -> dict[str, object] | None:
        """``update``, with what the run's state carries of the outbox from here."""
        carried = self._backend.outbox.carried(
            {
                **state,
                "messages": add_messages(
                    [*cast("list[BaseMessage]", state.get("messages", []))],
                    [*cast("list[BaseMessage]", update["messages"])],
                ),
            }
            if update is not None and "messages" in update
            else state
        )
        return update if carried is None else {**(update or {}), OUTBOX_KEY: carried}

    @hook_config(can_jump_to=["end"])
    async def abefore_agent(self, state: RunState, runtime: Runtime) -> dict[str, object] | None:
        return self._carrying(state, await self._before_agent(state, runtime))

    async def _before_agent(self, state: RunState, runtime: Runtime) -> dict[str, object] | None:
        trace = self._traces.get(self._backend.run)
        trace.start(self._trace_metadata())
        if self._backend.awaiting_adoption:
            # A worker never acts on its grant: it has no identity until it adopts one.
            thread_id = _RunConfigurable.model_validate(get_config().get("configurable", {}))
            assignment = next(
                (m.text for m in state["messages"] if isinstance(m, HumanMessage)), ""
            )
            await self._backend.adopt(thread_id.thread_id, run_id(), assignment)
        # What an earlier run left unsent goes out before anything this one does.
        self._backend.outbox.adopt(state)
        invocation = self._invocation()
        if state.get("open_swe_prepared_for") == invocation:
            trace.record_evidence(state.get(STATE_KEY, {}))
            return None
        opened = (
            await self._open_sandbox(runtime)
            if self._open_sandbox is not None and self._backend.run.planned.sandbox is not None
            else OpenedSandbox()
        )
        prepared = await self._phase(
            PREPARE,
            run_call(
                Prepare,
                run_id(),
                state,
                sandbox_id=opened.sandbox_id,
                sandbox_error=opened.error,
            ),
        )
        return {**_apply(prepared), "open_swe_prepared_for": invocation}

    @hook_config(can_jump_to=["end"])
    async def abefore_model(self, state: RunState, runtime: Runtime) -> dict[str, object] | None:  # noqa: ARG002
        self._backend.outbox.adopt(state)
        return self._carrying(state, await self._before_model(state))

    async def _before_model(self, state: RunState) -> dict[str, object] | None:
        inbox = await self._read_inbox()
        # The phase sees what the inbox brought, as the model will.
        with_inbox = {
            **state,
            "messages": [*state["messages"], *messages_from_dict(inbox.messages)],
            STATE_KEY: {**state.get(STATE_KEY, {}), **inbox.update},
        }
        planned = (
            await self._loop.before_model(with_inbox)
            if self._loop is not None
            else await self._phase(BEFORE_MODEL, run_call(RunCall, run_id(), with_inbox))
        )
        if planned.jump_to is not None:
            # No model call answers the inbox now: it stays for the run that comes next.
            return _apply(planned)
        if inbox.items.queued or inbox.items.events:
            # Alongside the call it is answered by, as the transcript's reports go.
            self._backend.outbox.received(InboxReceipt(run_id=run_id(), items=inbox.items))
        update = _apply(
            planned.model_copy(
                update={
                    "messages": [*inbox.messages, *planned.messages],
                    "update": {**inbox.update, **planned.update},
                }
            )
        )
        if planned.call is not None:
            update["open_swe_call"] = planned.call.model_dump()
        return update

    async def _read_inbox(self) -> Inbox:
        if self._inbox is None:
            return Inbox()
        # After the last call's receipt, so nothing it received comes again.
        await self._backend.outbox.receipts()
        return await call(self._backend, INBOX, InboxRead(run_id=run_id(), spec=self._inbox))

    async def _phase[Call: RunCall, Result: PhaseResult](
        self, endpoint: Endpoint[Call, Result], request: Call
    ) -> Result:
        await self._backend.outbox.drain()
        # A backend it cannot reach fails the run, as any failure there does, so every
        # surface reports it and the person can send the message again.
        return await call(self._backend, endpoint, request)

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse | ExtendedModelResponse:
        response = await self._call_model(request, handler)
        # The reply's report rides with the reply into the run's checkpoint.
        carried = self._backend.outbox.carried(request.state)
        if carried is None:
            return response
        return ExtendedModelResponse(
            model_response=response, command=Command(update={OUTBOX_KEY: carried})
        )

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        result = await handler(request)
        # The call's reports, and its subagent's, ride with its result into the checkpoint.
        messages = (
            [result]
            if isinstance(result, ToolMessage)
            else result.update.get("messages", [])
            if isinstance(result.update, dict)
            else []
        )
        carried = self._backend.outbox.carried(
            {**request.state, "messages": add_messages(request.state["messages"], messages)}
        )
        if carried is None:
            return result
        if isinstance(result, ToolMessage):
            return Command(update={"messages": [result], OUTBOX_KEY: carried})
        if isinstance(result.update, dict):
            return dataclasses.replace(result, update={**result.update, OUTBOX_KEY: carried})
        return result

    async def _call_model(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        own = self._own_model()
        if own is not None:
            # The run's own model, unless the loop names another for this call.
            request = request.override(model=own)
        raw = request.state.get("open_swe_call")
        if not isinstance(raw, Mapping):
            return await handler(request)
        planned = ModelCall.model_validate(raw)
        existing = request.system_message.text if request.system_message is not None else ""
        prompt = f"{planned.system_prompt}\n\n{existing}" if existing else planned.system_prompt
        request = request.override(system_message=SystemMessage(content=prompt))
        model = (
            planned.vision if planned.vision is not None and _has_image(request) else planned.model
        )
        if model is not None:
            request = request.override(model=build_model(model, use_gateway=planned.use_gateway))
        try:
            return await handler(request)
        except Exception as exc:
            if planned.fallback is None or _tool_pairing_error(exc):
                raise
            # As the in-process graph does: another provider before failing the run.
            logger.warning("Model call failed; retrying on the fallback model", exc_info=True)
            return await handler(
                request.override(
                    model=build_model(planned.fallback, use_gateway=planned.use_gateway)
                )
            )

    @hook_config(can_jump_to=["model", "end"])
    async def aafter_model(self, state: RunState, runtime: Runtime) -> dict[str, object] | None:  # noqa: ARG002
        self._backend.outbox.adopt(state)
        return self._carrying(state, await self._after_model(state))

    async def _after_model(self, state: RunState) -> dict[str, object] | None:
        if self._loop is None:
            return _apply(await self._phase(AFTER_MODEL, run_call(RunCall, run_id(), state)))
        last = state["messages"][-1] if state["messages"] else None
        # Only an answer that calls no tool may owe a reply or a CLI result.
        if not self._loop.looks_at_answers or (isinstance(last, AIMessage) and last.tool_calls):
            return None
        return _apply(await self._loop.after_model(state))

    async def aafter_agent(self, state: RunState, runtime: Runtime) -> dict[str, object] | None:  # noqa: ARG002
        self._backend.outbox.adopt(state)
        # What the backend has not taken by now waits in the thread's state for its next run.
        return self._carrying(state, await self._after_agent(state))

    async def _after_agent(self, state: RunState) -> dict[str, object] | None:
        metadata = self._trace_metadata()
        trace = self._traces.get(self._backend.run)
        try:
            if self._loop is not None:
                ended = await self._loop.after_agent(state, trace.evidence)
            else:
                request = run_call(RunCall, run_id(), state)
                ended = await self._phase(
                    AFTER_AGENT,
                    request.model_copy(update={"state": {**request.state, **trace.evidence}}),
                )
            metadata.update(ended.trace_metadata)
            return _apply(ended)
        except BackendCallError:
            logger.warning("The backend's end-of-run hooks failed", exc_info=True)
            return None
        finally:
            await trace.reconcile(metadata)


class ReportRepliesMiddleware(AgentMiddleware):
    """Hand each reply to the backend, where the graph's transcript and usage middleware see it.

    The run never waits for it: each reply is stamped here with what the run's usage
    counts it by, and the report goes out in line behind the tool calls it follows.
    """

    trace_policy = MIDDLEWARE_TRACE_POLICY

    def __init__(self, backend: OpenSweBackend) -> None:
        self._backend = backend

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        response = await handler(request)
        held = request.state.get(STATE_KEY)
        held = held if isinstance(held, Mapping) else {}
        route = held.get("model_route")
        route = route if isinstance(route, str) and route else None
        # Read per call: a worker learns its invocation only once it adopts its run.
        invocation_id = self._backend.invocation_id
        for message in response.result:
            if isinstance(message, AIMessage):
                message.response_metadata = {
                    **message.response_metadata,
                    **({"open_swe_model_route": route} if route else {}),
                    **(
                        {
                            "open_swe_invocation_id": invocation_id,
                            "open_swe_run_id": invocation_id,
                        }
                        if invocation_id
                        else {}
                    ),
                }
        offloading = held.get("conversation_offloading")
        inside = namespace()
        self._backend.outbox.replied(
            ModelResult(
                run_id=run_id(),
                namespace=inside,
                response=_json_dicts.validate_python(messages_to_dict(response.result)),
                # A subagent's own messages are its task, never something a person said.
                humans=[] if inside else human_messages(_since_last_reply(request.messages)),
                model_route=route,
                offloading=_json_object.validate_python(offloading)
                if isinstance(offloading, Mapping)
                else None,
            )
        )
        return response


def _since_last_reply(messages: Sequence[object]) -> Sequence[object]:
    """What the model is handed that it has not answered yet."""
    for index in range(len(messages) - 1, -1, -1):
        if isinstance(messages[index], AIMessage):
            return messages[index + 1 :]
    return messages


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
    report = tool_report(
        request.state,
        name=request.tool_call["name"],
        arguments=request.tool_call["args"],
        tool_call_id=tool_call_id,
    )
    backend.outbox.started(report)
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
        backend.outbox.finished(report, failure)
        raise
    backend.outbox.finished(report, _tool_message(result, tool_call_id))
    return result


class RuntimeToolsMiddleware(AgentMiddleware):
    """Report every tool call this runtime makes to the backend's transcript.

    ``FileToolsMiddleware`` reports the file and shell calls itself, as ``reported``
    is around the checks it applies; ``reported_elsewhere`` names them.
    """

    trace_policy = MIDDLEWARE_TRACE_POLICY

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

    trace_policy = MIDDLEWARE_TRACE_POLICY

    def __init__(self, backend: OpenSweBackend) -> None:
        self._backend = backend

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        result = await handler(request)
        if not self._backend.run.catalog.incident_evidence:
            return result
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

    trace_policy = MIDDLEWARE_TRACE_POLICY

    def __init__(self, backend: OpenSweBackend) -> None:
        self._backend = backend

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        tool_call = request.tool_call
        tool_call_id = tool_call.get("id") or ""
        if tool_call["name"] in _TRACKED_BY_OPEN_SWE:
            return _refused(
                tool_call["name"],
                tool_call_id,
                "Open SWE tracks its workers itself: use task_status, message_task_thread "
                "or control_worker instead.",
            )
        if tool_call["name"] != LAUNCH_WORKER_TOOL:
            return await handler(request)
        description = tool_call["args"].get("description")
        launch = Launch(
            tool_call_id=tool_call_id,
            instructions=description if isinstance(description, str) else "",
        )
        try:
            return await self._launch(request, handler, launch)
        except BackendCallError as exc:
            return _refused(tool_call["name"], tool_call_id, str(exc))

    async def _launch(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
        launch: Launch,
    ) -> ToolMessage:
        tool_call = request.tool_call
        tool_call_id = tool_call.get("id") or ""
        waits = iter(_LAUNCH_RETRY_SECONDS)
        while True:
            reply = await call(self._backend, LAUNCH_WORKER, launch)
            if reply.worker is not None:
                # A replay of a launch that already made its worker.
                return _worker_message(reply.worker, tool_call_id)
            if reply.grant is None or reply.assignment is None:
                raise BackendCallError("launch_worker returned no grant")
            # The worker is told its assignment as Open SWE words it, not the bare description.
            launched_call = ToolCall(
                name=tool_call["name"],
                args={**tool_call["args"], "description": reply.assignment},
                id=tool_call.get("id"),
            )
            with _LAUNCH_ERRORS.catching() as errors:
                result = await handler(
                    request.override(
                        tool_call=launched_call,
                        runtime=_launch_runtime(
                            request.runtime,
                            reply.grant,
                            # A worker runs on its coordinator's catalog until it adopts its own.
                            _json_object.validate_python(
                                self._backend.run.catalog.model_dump(mode="json")
                            ),
                        ),
                    )
                )
            if (launched := _launched_task(result)) is not None:
                return await self._register(launch, launched, tool_call_id)
            if any(map(_transient, errors)) and (wait := next(waits, None)) is not None:
                logger.warning(
                    "MDA did not start a task worker; launching it again",
                    extra={"tool_call_id": tool_call_id, "wait_seconds": wait},
                    exc_info=errors[-1],
                )
                await asyncio.sleep(wait)
                continue
            return _refused(tool_call["name"], tool_call_id, _not_launched(result, tool_call_id))

    async def _register(
        self, launch: Launch, launched: _Launched, tool_call_id: str
    ) -> ToolMessage:
        try:
            registered = await call(
                self._backend,
                WORKER_LAUNCHED,
                Launched(
                    **launch.model_dump(),
                    worker_thread_id=launched.thread_id,
                    worker_run_id=launched.run_id,
                ),
            )
        except BackendCallError:
            # The worker is running and registers itself when it adopts its run token;
            # reporting a failed launch would only start a second worker.
            logger.warning("The backend did not register a launched worker", exc_info=True)
            return _worker_message({"worker_thread_id": launched.thread_id}, tool_call_id)
        return _worker_message(registered.worker, tool_call_id)


def _launch_runtime(runtime: ToolRuntime, grant: str, catalog: dict[str, JsonValue]) -> ToolRuntime:
    """The tool runtime the launch sees: a worker grant for context, in place of the run's."""
    return dataclasses.replace(
        runtime, context={"graph": "agent", WORKER_GRANT_KEY: grant, CATALOG_KEY: catalog}
    )


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
