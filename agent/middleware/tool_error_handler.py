"""Tool error handling middleware.

Wraps all tool calls in try/except so that unhandled exceptions are returned as
error ToolMessages instead of crashing the agent run. A sandbox that stopped
answering is the exception: nothing the model does next can succeed, so the user
is notified and the error propagates.
"""

import json
import logging
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from langchain.agents.middleware.types import (
    AgentMiddleware,
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
from agent.run_config import RunConfig
from agent.sandboxes.retry import is_transient_sandbox_error

logger = logging.getLogger(__name__)

SANDBOX_TRANSIENT = "sandbox_transient"


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


class ToolErrorMiddleware(AgentMiddleware):
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
        try:
            return await handler(request)
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
                return _generic_error_tool_message(e, request)
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
