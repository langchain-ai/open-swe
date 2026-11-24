"""Return a failed subagent task to the parent's model without replaying the delegated task."""

import json
import logging
from collections.abc import Awaitable, Callable

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage
from langgraph.errors import GraphBubbleUp, NodeCancelledError
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command
from managed_deepagents import RunEndingError

from rt.tracing import MIDDLEWARE_TRACE_POLICY

logger = logging.getLogger(__name__)


class TaskErrorMiddleware(AgentMiddleware):
    """Let the parent's model handle a failed task, with what the provider said where the
    subagent's model call failed; only model calls may retry automatically.

    A failure that ends the run (``RunEndingError``, such as a sandbox that stopped
    answering), a pause and a cancellation go up unchanged.
    """

    trace_policy = MIDDLEWARE_TRACE_POLICY

    @staticmethod
    def _error_fields(exc: Exception) -> dict[str, object]:
        body = getattr(exc, "body", None)
        if not isinstance(body, dict):
            body = {}
        if isinstance(nested := body.get("error"), dict):
            body = nested
        status = getattr(exc, "status_code", None)
        if not isinstance(status, int):
            status = getattr(getattr(exc, "response", None), "status_code", None)
        out: dict[str, object] = {"error_type": type(exc).__name__, "message": str(exc)}
        if isinstance(status, int):
            out["status_code"] = status
        for key in ("type", "code", "message"):
            value = body.get(key)
            if not isinstance(value, str) or not value:
                value = getattr(exc, key, None)
            if isinstance(value, str) and value:
                out[key] = value
        return out

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        if request.tool_call["name"] != "task":
            return await handler(request)
        try:
            return await handler(request)
        except GraphBubbleUp, NodeCancelledError, RunEndingError:
            raise
        except Exception as exc:
            error = self._error_fields(exc)
            logger.warning(
                "Subagent task failed without replay",
                extra={"error_type": type(exc).__name__, "status_code": error.get("status_code")},
            )
            # A failed subagent saved its checkpoints under the `task` call's own namespace.
            # Naming it as deepagents does for one that finished lets a client load it too.
            namespace = (request.runtime.config.get("configurable") or {}).get("checkpoint_ns")
            return ToolMessage(
                content=json.dumps(
                    {"status": "failed", "source": "subagent", "error": error}, sort_keys=True
                ),
                tool_call_id=request.tool_call["id"],
                name="task",
                status="error",
                artifact={"checkpoint_ns": namespace} if namespace else None,
            )
