"""Tool errors from the calls this runtime runs in the box, as results the model can act on.

A box that stopped answering is the exception: nothing the model does next can
succeed, so the backend tells the person and the run ends. The backend handles
the errors of the tools it runs itself the same way.
"""

import json
import logging
import re
from collections.abc import Awaitable, Callable
from time import monotonic
from typing import Final

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage
from langgraph.errors import NodeCancelledError
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command
from langsmith.sandbox import (
    ResourceNotFoundError,
    SandboxConnectionError,
    SandboxRetryableConnectionError,
    SandboxServerReloadError,
)
from runtime_api.loop import SANDBOX_UNREACHABLE, SandboxGone

from rt.backend import OpenSweBackend
from rt.tools import run_id
from rt.tracing import MIDDLEWARE_TRACE_POLICY

logger = logging.getLogger(__name__)

_SANDBOX_ID: Final = re.compile(r"\bsb-[A-Za-z0-9-]+\b")
_SLOW_CALL_SECONDS: Final = 5


class ToolErrorMiddleware(AgentMiddleware):
    trace_policy = MIDDLEWARE_TRACE_POLICY

    def __init__(self, backend: OpenSweBackend) -> None:
        self._backend = backend

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        started = monotonic()
        try:
            return await handler(request)
        except NodeCancelledError:
            raise
        except SandboxRetryableConnectionError as exc:
            # The command never started, so nothing is known to be wrong with the box.
            logger.warning("The sandbox kept refusing a tool call before it started", exc_info=True)
            return _error(
                request,
                {
                    "error_type": type(exc).__name__,
                    "previous_error": str(exc),
                    "recovery": "sandbox_transient",
                    "error": (
                        "The sandbox connection was rejected before this command started, so "
                        "nothing ran and nothing changed."
                    ),
                },
            )
        except Exception as exc:
            if not _sandbox_unreachable(exc):
                logger.exception("Tool call failed", extra={"tool_name": request.tool_call["name"]})
                return _error(request, {"error": str(exc), "error_type": type(exc).__name__})
            # Every later call would meet the same dead box, so the run ends here once
            # the person knows.
            logger.exception("The thread's sandbox stopped answering")
            self._report_unreachable(request, exc)
            raise
        finally:
            seconds = monotonic() - started
            if seconds > _SLOW_CALL_SECONDS:
                logger.warning(
                    "Slow tool call",
                    extra={
                        "tool_name": request.tool_call["name"],
                        "tool_call_id": request.tool_call.get("id"),
                        "duration_seconds": seconds,
                    },
                )

    def _report_unreachable(self, request: ToolCallRequest, exc: Exception) -> None:
        sandbox_id = _SANDBOX_ID.search(str(exc))
        gone = SandboxGone(run_id=run_id(), sandbox_id=sandbox_id.group(0) if sandbox_id else None)
        self._backend.outbox.notify(SANDBOX_UNREACHABLE, gone)


def _sandbox_unreachable(exc: Exception) -> bool:
    """Whether the box itself did not answer, as opposed to a call that failed in it.

    A reload left the command running, and a missing resource that is not the box
    is a missing file.
    """
    if isinstance(exc, SandboxConnectionError):
        return not isinstance(exc, SandboxServerReloadError)
    return isinstance(exc, ResourceNotFoundError) and exc.resource_type == "sandbox"


def _error(request: ToolCallRequest, payload: dict[str, str]) -> ToolMessage:
    return ToolMessage(
        content=json.dumps({**payload, "status": "error", "name": request.tool_call["name"]}),
        tool_call_id=request.tool_call.get("id") or "",
        status="error",
    )
