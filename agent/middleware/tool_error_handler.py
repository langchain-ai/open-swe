"""Tool error handling middleware.

Wraps all tool calls in try/except so that unhandled exceptions are returned as
error ToolMessages instead of crashing the agent run. A sandbox that stopped
answering is the exception: nothing the model does next can succeed, so the user
is notified and the error propagates.
"""

import json
import logging
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, TypedDict, cast

from langchain.agents.middleware.types import (
    AgentState,
)
from langchain_core.messages import ToolMessage
from langgraph.config import get_config
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command
from langsmith.sandbox import (
    ResourceNotFoundError,
    SandboxConnectionError,
    SandboxServerReloadError,
)

from agent.middleware.sandbox_circuit_breaker import (
    extract_sandbox_id,
    post_sandbox_unreachable_notification,
)
from agent.middleware.trace import OpenSWEMiddleware
from agent.run_config import RunConfig
from agent.sandboxes.retry import is_transient_sandbox_error

logger = logging.getLogger(__name__)

SANDBOX_TRANSIENT = "sandbox_transient"
_IDENTICAL_FAILURES_KEY = "_tool_error_identical_failures"
_IDENTICAL_FAILURE_LIMIT = 3


class _FailureRecord(TypedDict):
    count: int
    error: str


def _get_name(candidate: object) -> str | None:
    if not candidate:
        return None
    if isinstance(candidate, str):
        return candidate
    if isinstance(candidate, dict):
        name = candidate.get("name")
    else:
        name = getattr(candidate, "name", None)
    return name if isinstance(name, str) and name else None


def _extract_tool_name(request: ToolCallRequest | None) -> str | None:
    if request is None:
        return None
    for attr in ("tool_call", "tool_name", "name"):
        name = _get_name(getattr(request, attr, None))
        if name:
            return name
    return None


def _to_error_payload(e: Exception, request: ToolCallRequest | None = None) -> dict[str, str]:
    data: dict[str, str] = {
        "error": str(e),
        "error_type": e.__class__.__name__,
        "status": "error",
    }
    tool_name = _extract_tool_name(request)
    if tool_name:
        data["name"] = tool_name
    return data


def _to_transient_sandbox_payload(
    e: Exception,
    request: ToolCallRequest | None = None,
) -> dict[str, str]:
    data: dict[str, str] = {
        "status": "error",
        "error_type": e.__class__.__name__,
        "previous_error": str(e),
        "recovery": SANDBOX_TRANSIENT,
        "error": (
            "The sandbox connection was rejected before this command started, so "
            "nothing ran and nothing changed."
        ),
    }
    sandbox_id = extract_sandbox_id(str(e))
    if sandbox_id:
        data["sandbox_id"] = sandbox_id
    tool_name = _extract_tool_name(request)
    if tool_name:
        data["name"] = tool_name
    return data


def _is_sandbox_unreachable(e: Exception) -> bool:
    """Whether the failure means the sandbox itself did not answer.

    A connection error does, once the two that carry their own meaning are
    excluded: a retryable rejection never started the command, and a server
    reload left it running. ``ResourceNotFoundError`` qualifies only for the
    sandbox itself — a missing file is a tool-local failure.
    """
    if isinstance(e, SandboxConnectionError):
        return not isinstance(e, SandboxServerReloadError)
    return isinstance(e, ResourceNotFoundError) and e.resource_type == "sandbox"


def _get_tool_call_id(request: ToolCallRequest) -> str | None:
    if isinstance(request.tool_call, dict):
        return request.tool_call.get("id")
    return None


def _get_run_config(request: ToolCallRequest) -> Mapping[str, Any] | None:
    runtime_config = getattr(getattr(request, "runtime", None), "config", None)
    if isinstance(runtime_config, Mapping):
        return runtime_config
    try:
        maybe_config = get_config()
    except Exception:
        logger.exception("Failed to read runnable config while handling sandbox error")
        return None
    return maybe_config if isinstance(maybe_config, Mapping) else None


def _get_thread_id(request: ToolCallRequest) -> str | None:
    config = _get_run_config(request)
    if config is None:
        return None
    return RunConfig.from_config(config).thread_id or None


def _transient_sandbox_tool_message(
    e: Exception,
    request: ToolCallRequest,
) -> ToolMessage:
    data = _to_transient_sandbox_payload(e, request)
    return ToolMessage(
        content=json.dumps(data),
        tool_call_id=_get_tool_call_id(request),
        status="error",
    )


def _generic_error_tool_message(e: Exception, request: ToolCallRequest) -> ToolMessage:
    data = _to_error_payload(e, request)
    return ToolMessage(
        content=json.dumps(data),
        tool_call_id=_get_tool_call_id(request),
        status="error",
    )


def _failure_key(request: ToolCallRequest) -> str | None:
    tool_name = _extract_tool_name(request)
    if tool_name is None or not isinstance(request.tool_call, dict):
        return None
    return json.dumps(
        [tool_name, request.tool_call.get("args", {})],
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _failure_records(request: ToolCallRequest) -> dict[str, _FailureRecord] | None:
    if not isinstance(request.state, dict):
        return None
    records = request.state.get(_IDENTICAL_FAILURES_KEY)
    if not isinstance(records, dict):
        records = {}
        request.state[_IDENTICAL_FAILURES_KEY] = records
    return cast(dict[str, _FailureRecord], records)


def _error_text(result: ToolMessage) -> str:
    if isinstance(result.content, str):
        return result.content
    return json.dumps(result.content, sort_keys=True, default=str)


def _record_tool_result(request: ToolCallRequest, result: ToolMessage) -> None:
    key = _failure_key(request)
    records = _failure_records(request)
    if key is None or records is None:
        return
    if result.status != "error":
        records.pop(key, None)
        return
    error = _error_text(result)
    previous = records.get(key)
    records[key] = {
        "count": previous["count"] + 1 if previous and previous["error"] == error else 1,
        "error": error,
    }


def _identical_failure_message(request: ToolCallRequest) -> ToolMessage | None:
    key = _failure_key(request)
    records = _failure_records(request)
    if key is None or records is None:
        return None
    record = records.get(key)
    if record is None or record["count"] < _IDENTICAL_FAILURE_LIMIT:
        return None
    return ToolMessage(
        content=(
            "This exact tool call has failed repeatedly and cannot succeed as written. "
            "Change the arguments, use a different tool, or report the blocker to the user."
        ),
        tool_call_id=_get_tool_call_id(request),
        status="error",
    )


class ToolErrorMiddleware(OpenSWEMiddleware):
    """Normalize tool execution errors into predictable payloads.

    Catches any exception thrown during a tool call and converts it into
    a ToolMessage with status="error" so the LLM can see the failure and
    self-correct, rather than crashing the entire agent run.

    An unreachable sandbox is the one error that is not survivable, so it is
    re-raised instead: every later sandbox call would fail the same way and
    notify the user again.
    """

    state_schema = AgentState

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        if blocked := _identical_failure_message(request):
            return blocked
        try:
            result = await handler(request)
            if isinstance(result, ToolMessage):
                _record_tool_result(request, result)
            return result
        except Exception as e:
            # The command never started, so nothing is known to be wrong with the
            # sandbox: ending the run here would turn a gateway blip into an
            # abandoned one.
            if is_transient_sandbox_error(e):
                logger.warning(
                    "Transient sandbox error during tool call; request=%r", request, exc_info=True
                )
                return _transient_sandbox_tool_message(e, request)
            if not _is_sandbox_unreachable(e):
                logger.exception("Error during tool call handling; request=%r", request)
                result = _generic_error_tool_message(e, request)
                _record_tool_result(request, result)
                return result
            logger.exception("Sandbox error during tool call handling; request=%r", request)
            thread_id = _get_thread_id(request)
            config = _get_run_config(request)
            if config is not None:
                try:
                    await post_sandbox_unreachable_notification(
                        config, sandbox_id=extract_sandbox_id(str(e))
                    )
                except Exception:
                    logger.exception("Failed to notify user of dead sandbox for %s", thread_id)
            # Every later sandbox call would hit the same dead backend and notify
            # again, so end the run here now that the user has been told once.
            raise
