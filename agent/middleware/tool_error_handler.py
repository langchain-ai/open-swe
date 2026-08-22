"""Tool error handling middleware.

Wraps all tool calls in try/except so that unhandled exceptions are
returned as error ToolMessages instead of crashing the agent run.
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
from langsmith.sandbox import SandboxClientError

from ..sandboxes.registry import clear_sandbox_backend
from ..utils.source_channel import (
    extract_sandbox_id,
    post_sandbox_unreachable_notification,
)

logger = logging.getLogger(__name__)

SANDBOX_UNREACHABLE = "sandbox_unreachable"


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


def _to_sandbox_unreachable_payload(
    e: SandboxClientError,
    request: ToolCallRequest | None = None,
) -> dict[str, str]:
    sandbox_id = extract_sandbox_id(str(e))
    which = f" ({sandbox_id})" if sandbox_id else ""
    data: dict[str, str] = {
        "status": "error",
        "error_type": e.__class__.__name__,
        "previous_error": str(e),
        "recovery": SANDBOX_UNREACHABLE,
        "error": (
            f"The sandbox for this thread{which} stopped responding. It will not be "
            "replaced automatically: a fresh sandbox is empty, so swapping one in "
            "would discard any uncommitted work while looking like a recovery. Stop "
            "calling sandbox tools and tell the user their sandbox stopped "
            "responding, naming it, and that retriggering the thread retries the "
            "same sandbox while a new thread gets a fresh one."
        ),
    }
    if sandbox_id:
        data["sandbox_id"] = sandbox_id
    tool_name = _extract_tool_name(request)
    if tool_name:
        data["name"] = tool_name
    return data


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
    configurable = config.get("configurable", {})
    if not isinstance(configurable, Mapping):
        return None
    thread_id = configurable.get("thread_id")
    return thread_id if isinstance(thread_id, str) and thread_id else None


def _sandbox_unreachable_tool_message(
    e: SandboxClientError,
    request: ToolCallRequest,
) -> ToolMessage:
    data = _to_sandbox_unreachable_payload(e, request)
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
    """

    state_schema = AgentState

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        try:
            return await handler(request)
        except SandboxClientError as e:
            logger.exception("Sandbox error during tool call handling; request=%r", request)
            thread_id = _get_thread_id(request)
            if thread_id:
                clear_sandbox_backend(thread_id)
            config = _get_run_config(request)
            if config is not None:
                try:
                    await post_sandbox_unreachable_notification(
                        config, sandbox_id=extract_sandbox_id(str(e))
                    )
                except Exception:
                    logger.exception("Failed to notify user of dead sandbox for %s", thread_id)
            return _sandbox_unreachable_tool_message(e, request)
        except Exception as e:
            logger.exception("Error during tool call handling; request=%r", request)
            return _generic_error_tool_message(e, request)
