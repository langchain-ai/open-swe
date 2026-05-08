"""Tool error handling middleware.

Wraps all tool calls in try/except so that unhandled exceptions are
returned as error ToolMessages instead of crashing the agent run.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable

from langchain.agents.middleware.types import (
    AgentMiddleware,
    AgentState,
)
from langchain_core.messages import ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command
from langsmith.sandbox import SandboxClientError

logger = logging.getLogger(__name__)


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


def _get_tool_call_id(request: ToolCallRequest) -> str | None:
    if isinstance(request.tool_call, dict):
        return request.tool_call.get("id")
    return None


def _get_thread_id(request: ToolCallRequest) -> str | None:
    config = getattr(request, "config", None)
    if not isinstance(config, dict):
        return None
    configurable = config.get("configurable")
    if not isinstance(configurable, dict):
        return None
    thread_id = configurable.get("thread_id")
    return thread_id if isinstance(thread_id, str) and thread_id else None


def _sandbox_recreated_message(
    new_backend_id: str | None,
    original_error: Exception,
    request: ToolCallRequest,
) -> ToolMessage:
    """Build a ToolMessage instructing the LLM to retry after sandbox recreation."""
    new_id_text = new_backend_id or "<unknown>"
    payload: dict[str, str] = {
        "status": "error",
        "error_type": "SandboxClientError",
        "error": (
            f"Sandbox was unreachable and has been recreated as sb-{new_id_text}. "
            f"Retry the last call. ({original_error})"
        ),
        "sandbox_recreated": "true",
        "new_sandbox_id": new_id_text,
    }
    tool_name = _extract_tool_name(request)
    if tool_name:
        payload["name"] = tool_name
    return ToolMessage(
        content=json.dumps(payload),
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

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command],
    ) -> ToolMessage | Command:
        try:
            return handler(request)
        except SandboxClientError as e:
            # Mid-run sandbox death — try to recreate the backend before falling
            # through to the generic ToolMessage path. Otherwise the agent will
            # retry against the same dead sb-<id> indefinitely (see issue
            # 7a78d721 / open-swe-v3 traces 019e0420, 019e04fd, 019e050e).
            recreated = self._recreate_sandbox_sync(request)
            if recreated is not None:
                return _sandbox_recreated_message(recreated, e, request)
            logger.exception(
                "SandboxClientError during tool call and recreation failed; request=%r",
                request,
            )
            data = _to_error_payload(e, request)
            return ToolMessage(
                content=json.dumps(data),
                tool_call_id=_get_tool_call_id(request),
                status="error",
            )
        except Exception as e:
            logger.exception("Error during tool call handling; request=%r", request)
            data = _to_error_payload(e, request)
            return ToolMessage(
                content=json.dumps(data),
                tool_call_id=_get_tool_call_id(request),
                status="error",
            )

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        try:
            return await handler(request)
        except SandboxClientError as e:
            # Mid-run sandbox death — see wrap_tool_call comment above.
            recreated = await self._arecreate_sandbox(request)
            if recreated is not None:
                return _sandbox_recreated_message(recreated, e, request)
            logger.exception(
                "SandboxClientError during tool call and recreation failed; request=%r",
                request,
            )
            data = _to_error_payload(e, request)
            return ToolMessage(
                content=json.dumps(data),
                tool_call_id=_get_tool_call_id(request),
                status="error",
            )
        except Exception as e:
            logger.exception("Error during tool call handling; request=%r", request)
            data = _to_error_payload(e, request)
            return ToolMessage(
                content=json.dumps(data),
                tool_call_id=_get_tool_call_id(request),
                status="error",
            )

    @staticmethod
    async def _arecreate_sandbox(request: ToolCallRequest) -> str | None:
        """Recreate the sandbox for this thread; return new sandbox id or None."""
        thread_id = _get_thread_id(request)
        if not thread_id:
            logger.warning(
                "Cannot recover from SandboxClientError: thread_id missing from request config"
            )
            return None
        # Local import to avoid circular import (server.py imports this module).
        try:
            from agent.server import _recreate_sandbox  # noqa: PLC0415
        except Exception:
            logger.exception("Failed to import _recreate_sandbox for mid-run recovery")
            return None
        try:
            new_backend = await _recreate_sandbox(thread_id)
        except Exception:
            logger.exception(
                "Sandbox recreation failed mid-run for thread %s", thread_id
            )
            return None
        # Update the shared cache so subsequent calls (and the next agent step)
        # pick up the new handle.
        try:
            from agent.utils.sandbox_state import SANDBOX_BACKENDS  # noqa: PLC0415

            SANDBOX_BACKENDS[thread_id] = new_backend
        except Exception:
            logger.exception("Failed to update SANDBOX_BACKENDS cache after recreation")
        return getattr(new_backend, "id", None)

    @staticmethod
    def _recreate_sandbox_sync(request: ToolCallRequest) -> str | None:
        """Sync wrapper for _arecreate_sandbox."""
        import asyncio  # noqa: PLC0415

        try:
            return asyncio.run(ToolErrorMiddleware._arecreate_sandbox(request))
        except RuntimeError:
            # Already inside an event loop — fall back to a fresh loop.
            try:
                loop = asyncio.new_event_loop()
                try:
                    return loop.run_until_complete(
                        ToolErrorMiddleware._arecreate_sandbox(request)
                    )
                finally:
                    loop.close()
            except Exception:
                logger.exception("Failed sync sandbox recreation")
                return None
