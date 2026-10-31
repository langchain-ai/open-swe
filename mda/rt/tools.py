"""The run's tools, each one calling an operation of the Open SWE backend for its work.

They carry the names, descriptions and argument schemas the backend gives its
operations, so the prompts, the transcript and the dashboard see what an
in-process run shows. The tool call is this runtime's: it sends the arguments, the
run state the operation reads and, read as the run, the credentials of the MDA
connections its tool uses, then turns what the operation answers into the tool's
result and its state updates. The file and shell tools are this runtime's own
(``rt.file_tools``).
"""

import logging
import time
from collections.abc import Awaitable, Callable, Mapping
from functools import partial
from typing import Final
from uuid import uuid4

from langchain.tools import ToolRuntime
from langchain_core.messages import BaseMessage, ToolMessage, messages_to_dict
from langchain_core.tools import StructuredTool
from langchain_quickjs import UnresolvedToolError
from langgraph.config import get_config
from langgraph.types import Command
from managed_deepagents import (
    ConnectionNotFoundError,
    CredentialAuthorizationRequiredError,
    CredentialStoreError,
    connections,
)
from pydantic import JsonValue, TypeAdapter
from runtime_api.catalog import ToolSpec
from runtime_api.endpoint import LONG_CALL_SECONDS, call
from runtime_api.loop import ToolCallReport
from runtime_api.operations import (
    OPERATION,
    RECORD_EVIDENCE,
    OperationResult,
    RunOperation,
    ToolEvidence,
)

from rt.backend import (
    TOOL_CALL_SLACK_SECONDS,
    BackendCallError,
    BackendTimeoutError,
    BackendUnavailableError,
    OpenSweBackend,
    held_state,
    issuing_message_id,
    namespace,
    reported_call,
)
from rt.git_evidence import turn_git

logger = logging.getLogger(__name__)

STATE_KEY: Final = "open_swe_state"
_TIMED_OUT: Final = (
    "Error: Open SWE did not answer this tool call in time. Whatever it started may "
    "still be running in the sandbox; check before running it again."
)
_UNREACHABLE: Final = (
    "Error: The Open SWE backend could not be reached for this tool call. "
    "It may or may not have run; check state before retrying."
)
_NO_CONNECTION: Final = (
    "Error: This tool reads its credential from the deployment's {slug} connection, which "
    "is not set up, so it did not run. An admin creates it with `mda connections create {slug}`."
)
_UNREAD_CONNECTION: Final = (
    "Error: This tool could not read its credential from the deployment's connections, so "
    "it did not run: {reason}"
)
_json = TypeAdapter(JsonValue)
_json_list = TypeAdapter(list[dict[str, JsonValue]])
_json_object = TypeAdapter(dict[str, JsonValue])
_json_content = TypeAdapter(str | list[JsonValue])


class SandboxGoneError(RuntimeError):
    """The thread's sandbox did not answer an operation; the person has been told, and the run ends."""


class UnreadConnectionError(RuntimeError):
    """A connection of the deployment's an operation needs could not be read, so it did not run."""

    async def result(self, tool_call_id: str) -> ToolMessage | Command:
        """The failed tool call, which tells the model why."""
        return ToolMessage(content=str(self), tool_call_id=tool_call_id, status="error")


def run_id() -> str | None:
    """The Agent Server run this graph is executing, as the backend keys it."""
    config = get_config()
    configurable = config.get("configurable") or {}
    raw = configurable.get("run_id") or config.get("metadata", {}).get("run_id")
    return str(raw) if raw else None


def tool_report(
    state: Mapping[str, object],
    *,
    name: str,
    arguments: Mapping[str, JsonValue],
    tool_call_id: str,
) -> ToolCallReport:
    """A tool call as the backend's transcript takes it."""
    return ToolCallReport(
        run_id=run_id(),
        namespace=namespace(),
        name=name,
        arguments=dict(arguments),
        tool_call_id=tool_call_id,
        message_id=issuing_message_id(state, tool_call_id),
    )


def _reading(spec: ToolSpec, state: Mapping[str, object]) -> RunOperation:
    """A call of ``spec``'s operation, carrying the run state it says it reads."""
    run_state = held_state(state)
    if (spec.reads_all or "async_tasks" in spec.reads) and "async_tasks" in state:
        run_state["async_tasks"] = _json_object.validate_python(state["async_tasks"])
    keys = list(run_state) if spec.reads_all else [k for k in spec.reads if k != "messages"]
    messages = state.get("messages")
    conversation = (
        [m for m in messages if isinstance(m, BaseMessage)] if isinstance(messages, list) else []
    )
    return RunOperation(
        name=spec.name,
        request_id="",
        state={key: run_state[key] for key in keys if key in run_state},
        messages=_json_list.validate_python(messages_to_dict(conversation))
        if spec.reads_all or "messages" in spec.reads
        else None,
    )


def _seconds(spec: ToolSpec, arguments: Mapping[str, JsonValue]) -> float:
    # A call with its own timeout, such as a long command, must be able to run it out
    # and answer that it did, rather than outlive this one.
    requested = arguments.get("timeout")
    if isinstance(requested, int | float) and not isinstance(requested, bool):
        return max(LONG_CALL_SECONDS, requested + TOOL_CALL_SLACK_SECONDS)
    return spec.seconds if spec.seconds is not None else LONG_CALL_SECONDS


def _content(content: str | list[JsonValue]) -> str | list[str | dict]:
    if isinstance(content, str):
        return content
    return [block for block in content if isinstance(block, str | dict)]


def _result(answer: OperationResult, tool_call_id: str, name: str) -> ToolMessage | Command:
    message = ToolMessage(
        content=_content(answer.content),
        status=answer.status,
        artifact=answer.artifact,
        tool_call_id=tool_call_id,
        name=name,
        id=answer.message_id,
    )
    if not answer.state:
        return message
    return Command(update={"messages": [message], STATE_KEY: answer.state})


async def cited(
    backend: OpenSweBackend, name: str, arguments: Mapping[str, object], result: ToolMessage
) -> ToolMessage:
    """``result``, recorded as incident evidence, citing the id it was recorded under."""
    evidence = ToolEvidence(
        run_id=run_id(),
        tool=name,
        arguments=_json_object.validate_python(arguments),
        content=_json_content.validate_python(result.content),
        status=result.status,
        artifact=_json.validate_python(result.artifact) if result.artifact is not None else None,
    )
    try:
        reply = await call(backend, RECORD_EVIDENCE, evidence)
    except BackendCallError:
        logger.warning("Could not record a tool result as incident evidence", exc_info=True)
        return result
    return result.model_copy(update={"content": _content(reply.content)})


async def _credentials(spec: ToolSpec) -> dict[str, str]:
    """What the run reads of each connection ``spec`` names, by slug.

    One of the deployment's it cannot read raises ``UnreadConnectionError``.

    A person who has not granted a user connection is asked to, and the run pauses
    until they answer (``GraphBubbleUp``). A user connection the run cannot read
    otherwise (it acts for no person, they would not grant it, Agent Auth could not
    be reached) is left out, and the operation goes on as it did without it.
    """
    credentials: dict[str, str] = {}
    for ref in spec.connections:
        extra = {"operation": spec.name, "connection": ref.slug}
        try:
            credentials[ref.slug] = await connections.get(ref.slug, {"type": ref.owner})
        except ConnectionNotFoundError as exc:
            logger.warning("An operation's connection is not set up", extra=extra)
            raise UnreadConnectionError(_NO_CONNECTION.format(slug=ref.slug)) from exc
        except (CredentialStoreError, CredentialAuthorizationRequiredError) as exc:
            if ref.owner == "agent":
                # Its message quotes no more of Agent Auth's answer than the problem's
                # detail and remedy, never a credential, so the model may read it.
                logger.warning(
                    "Could not read an operation's connection", extra=extra, exc_info=True
                )
                raise UnreadConnectionError(_UNREAD_CONNECTION.format(reason=exc)) from exc
            logger.info(
                "The run reads no user connection for an operation",
                extra={**extra, "reason": str(exc)},
            )
        except OSError as exc:
            # The SDK raises ``urllib.error.URLError`` when Agent Auth does not answer.
            logger.warning(
                "Could not reach Agent Auth for an operation's connection",
                extra=extra,
                exc_info=True,
            )
            if ref.owner == "agent":
                raise UnreadConnectionError(
                    _UNREAD_CONNECTION.format(reason="Agent Auth could not be reached.")
                ) from exc
    return credentials


async def _operate(
    backend: OpenSweBackend,
    spec: ToolSpec,
    arguments: Mapping[str, JsonValue],
    runtime: ToolRuntime,
    tool_call_id: str,
    credentials: dict[str, str],
) -> ToolMessage | Command:
    request = _reading(spec, runtime.state).model_copy(
        update={
            "run_id": run_id(),
            "arguments": dict(arguments),
            "request_id": tool_call_id,
            "grant": backend.grant(spec),
            "credentials": credentials,
        }
    )
    started = time.time_ns()
    try:
        answer = await call(backend, OPERATION, request, timeout=_seconds(spec, arguments))
    except BackendTimeoutError:
        logger.warning("The backend did not answer an operation", exc_info=True)
        return ToolMessage(content=_TIMED_OUT, tool_call_id=tool_call_id, status="error")
    except BackendUnavailableError:
        logger.warning("The backend could not complete an operation")
        return ToolMessage(content=_UNREACHABLE, tool_call_id=tool_call_id, status="error")
    if answer.sandbox_unreachable:
        raise SandboxGoneError(f"The thread's sandbox did not answer {spec.name}")
    for event in answer.events:
        runtime.stream_writer(event)
    turn_git(backend.run).found_pull_requests(answer.pull_requests, started)
    return _result(answer, tool_call_id, spec.name)


async def called(
    backend: OpenSweBackend,
    name: str,
    arguments: Mapping[str, JsonValue],
    runtime: ToolRuntime,
    make: Callable[[str], Awaitable[ToolMessage | Command]],
    *,
    cites_evidence: bool = False,
) -> ToolMessage | Command:
    """Make one call of tool ``name`` with ``arguments``: ``make``, given the call's id.

    Code mode calls tools directly, past the agent's tool-call chain that reports each
    call and cites it as incident evidence, so a call it makes does both itself, under
    the code mode call that ran it.
    """
    outer = reported_call()
    if outer is None or outer == runtime.tool_call_id:
        return await make(runtime.tool_call_id or "")
    tool_call_id = f"{outer}:{name}:{uuid4().hex[:8]}"
    report = tool_report(runtime.state, name=name, arguments=arguments, tool_call_id=tool_call_id)
    backend.outbox.started(report)
    try:
        result = await make(tool_call_id)
    except Exception as exc:
        failure = ToolMessage(
            content=f"{type(exc).__name__}: {exc}", tool_call_id=tool_call_id, status="error"
        )
        backend.outbox.finished(report, failure)
        raise
    message = result if isinstance(result, ToolMessage) else _answer(result, tool_call_id)
    if cites_evidence:
        message = await cited(backend, name, arguments, message)
    backend.outbox.finished(report, message)
    if isinstance(result, ToolMessage):
        return message
    return Command(update={**_update(result), "messages": [message]})


def _answer(result: Command, tool_call_id: str) -> ToolMessage:
    messages = _update(result).get("messages")
    return next(
        (
            m
            for m in (messages if isinstance(messages, list) else [])
            if isinstance(m, ToolMessage) and m.tool_call_id == tool_call_id
        ),
        ToolMessage(content="", tool_call_id=tool_call_id),
    )


def _update(result: Command) -> dict[str, object]:
    return dict(result.update) if isinstance(result.update, Mapping) else {}


async def run_operation(
    backend: OpenSweBackend,
    spec: ToolSpec,
    arguments: Mapping[str, JsonValue],
    runtime: ToolRuntime,
    *,
    cites_evidence: bool = False,
) -> ToolMessage | Command:
    """Call ``spec``'s operation with ``arguments``, for one tool call, with what the run
    reads of the connections it names."""
    # Read before code mode reports the call: a pause for a grant runs the code again,
    # which reports the call under a new id, so a report made first would never finish.
    try:
        credentials = await _credentials(spec)
    except UnreadConnectionError as exc:
        make = exc.result
    else:
        make = partial(_operate, backend, spec, arguments, runtime, credentials=credentials)
    return await called(backend, spec.name, arguments, runtime, make, cites_evidence=cites_evidence)


def operation_tool(
    spec: ToolSpec, backend: OpenSweBackend, *, cites_evidence: bool = False
) -> StructuredTool:
    async def operate(runtime: ToolRuntime, **arguments: JsonValue) -> ToolMessage | Command:
        return await run_operation(backend, spec, arguments, runtime, cites_evidence=cites_evidence)

    return StructuredTool.from_function(
        coroutine=operate,
        name=spec.name,
        description=spec.description,
        args_schema=spec.parameters,
    )


# Any arguments: what the tool accepts is the backend's to check.
_ANY_ARGUMENTS: Final[dict[str, JsonValue]] = {"type": "object", "additionalProperties": True}


def backend_tool(
    name: str,
    backend: OpenSweBackend,
    resolve: Callable[[str], Awaitable[ToolSpec | None]],
) -> StructuredTool:
    """A tool the run's catalog leaves out, called as ``name``.

    The run calls it by the name ``search_tools`` gave, from code mode or directly;
    ``resolve`` asks the backend which tool that is, with the run's grant of it.
    Made for no run in particular: the call finds its run.
    """

    async def operate(runtime: ToolRuntime, **arguments: JsonValue) -> ToolMessage | Command:
        spec = await resolve(name)
        if spec is None:
            # Code mode throws it as a TypeError, as for any name that is not a function.
            raise UnresolvedToolError(
                f"{name} is not one of this run's tools; find the tool you want with search_tools"
            )
        cites_evidence = backend.run.catalog.incident_evidence
        return await run_operation(backend, spec, arguments, runtime, cites_evidence=cites_evidence)

    return StructuredTool.from_function(
        coroutine=operate,
        name=name,
        description=f"The run's tool it calls as {name}.",
        args_schema=_ANY_ARGUMENTS,
    )
