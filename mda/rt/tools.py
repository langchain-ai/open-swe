"""The run's tools, each one calling an operation of the Open SWE backend for its work.

They carry the names, descriptions and argument schemas the backend gives its
operations, so the prompts, the transcript and the dashboard see what an
in-process run shows. The tool call is this runtime's: it sends the arguments and
the run state the operation reads, then turns what the operation answers into the
tool's result and its state updates. The file and shell tools are this runtime's
own (``rt.file_tools``).
"""

import logging
from collections.abc import Mapping
from typing import Final

from langchain.tools import ToolRuntime
from langchain_core.messages import BaseMessage, ToolMessage, messages_to_dict
from langchain_core.tools import StructuredTool
from langgraph.config import get_config
from langgraph.types import Command
from pydantic import JsonValue, TypeAdapter
from runtime_api.catalog import OperationSpec
from runtime_api.endpoint import call
from runtime_api.operations import OPERATION, OperationResult, RunOperation

from rt.backend import (
    LONG_CALL_SECONDS,
    TOOL_CALL_SLACK_SECONDS,
    BackendTimeoutError,
    BackendUnavailableError,
    OpenSweBackend,
    run_body,
)
from rt.tracing import TurnTrace

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
_json_list = TypeAdapter(list[dict[str, JsonValue]])


class SandboxGoneError(RuntimeError):
    """The thread's box did not answer an operation; the person has been told, and the run ends."""


def run_id() -> str | None:
    """The Agent Server run this graph is executing, as the backend keys it."""
    config = get_config()
    configurable = config.get("configurable") or {}
    raw = configurable.get("run_id") or config.get("metadata", {}).get("run_id")
    return str(raw) if raw else None


def tool_body(
    state: Mapping[str, object],
    *,
    name: str,
    arguments: Mapping[str, JsonValue],
    tool_call_id: str,
) -> dict[str, JsonValue]:
    """A tool call as the backend's transcript takes it, with the turn's git evidence."""
    body = run_body(
        run_id(), state, name=name, arguments=dict(arguments), tool_call_id=tool_call_id
    )
    held = body.get("state")
    body["state"] = {**(held if isinstance(held, dict) else {}), **TurnTrace.current_evidence()}
    return body


def _reading(spec: OperationSpec, state: Mapping[str, object]) -> RunOperation:
    """A call of ``spec``'s operation, carrying the run state it says it reads."""
    held = run_body(None, state).get("state")
    run_state = held if isinstance(held, dict) else {}
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


def _seconds(arguments: Mapping[str, JsonValue]) -> float:
    # A call with its own timeout, such as a long command, must be able to run it out
    # and answer that it did, rather than outlive this one.
    requested = arguments.get("timeout")
    if isinstance(requested, int | float) and not isinstance(requested, bool):
        return max(LONG_CALL_SECONDS, requested + TOOL_CALL_SLACK_SECONDS)
    return LONG_CALL_SECONDS


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
    if answer.evidence:
        TurnTrace.record_evidence(answer.evidence)
    if not answer.state:
        return message
    return Command(update={"messages": [message], STATE_KEY: answer.state})


async def run_operation(
    backend: OpenSweBackend,
    spec: OperationSpec,
    arguments: Mapping[str, JsonValue],
    runtime: ToolRuntime,
) -> ToolMessage | Command:
    """Call ``spec``'s operation with ``arguments``, for one tool call."""
    tool_call_id = runtime.tool_call_id or ""
    request = _reading(spec, runtime.state).model_copy(
        update={"run_id": run_id(), "arguments": dict(arguments), "request_id": tool_call_id}
    )
    try:
        answer = await call(backend, OPERATION, request, timeout=_seconds(arguments))
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
    return _result(answer, tool_call_id, spec.name)


def operation_tool(spec: OperationSpec, backend: OpenSweBackend) -> StructuredTool:
    async def operate(runtime: ToolRuntime, **arguments: JsonValue) -> ToolMessage | Command:
        return await run_operation(backend, spec, arguments, runtime)

    return StructuredTool.from_function(
        coroutine=operate,
        name=spec.name,
        description=spec.description,
        args_schema=spec.parameters,
    )
