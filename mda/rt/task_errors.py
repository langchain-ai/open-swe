"""Return recoverable subagent failures without replaying the delegated task."""

import json
import logging
from collections.abc import Awaitable, Callable

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage
from langgraph.errors import GraphBubbleUp
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

from rt.model_calls import RETRYABLE_STATUS_CODES, is_transient
from rt.tracing import MIDDLEWARE_TRACE_POLICY

logger = logging.getLogger(__name__)

_RETURN_TO_MODEL_CODES = frozenset({"invalid_prompt", "context_length_exceeded"})
_RETURN_TO_MODEL_STATUS_CODES = frozenset({400, 422})
_OTHER_SDK_TRANSIENT_NAMES = frozenset({"APIConnectionError", "APITimeoutError"})


class TaskErrorMiddleware(AgentMiddleware):
    """Let the parent handle failed tasks; only model calls may retry automatically."""

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

    @staticmethod
    def _recoverable(exc: Exception, error: dict[str, object]) -> bool:
        if is_transient(exc) or type(exc).__name__ in _OTHER_SDK_TRANSIENT_NAMES:
            return True
        status = error.get("status_code")
        if isinstance(status, int) and (status in RETRYABLE_STATUS_CODES or status >= 500):
            return True
        code = error.get("code")
        return code in _RETURN_TO_MODEL_CODES or (
            code is None
            and error.get("type") == "invalid_request_error"
            and isinstance(status, int)
            and status in _RETURN_TO_MODEL_STATUS_CODES
        )

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        if request.tool_call["name"] != "task":
            return await handler(request)
        try:
            return await handler(request)
        except GraphBubbleUp:
            raise
        except Exception as exc:
            error = self._error_fields(exc)
            if not self._recoverable(exc, error):
                raise
            logger.warning(
                "Subagent task failed without replay",
                extra={"error_type": type(exc).__name__, "status_code": error.get("status_code")},
            )
            return ToolMessage(
                content=json.dumps(
                    {"status": "failed", "source": "subagent", "error": error}, sort_keys=True
                ),
                tool_call_id=request.tool_call["id"],
                name="task",
                status="error",
            )
