"""The graph's tools, each one a call to the Open SWE backend.

They carry the names, descriptions and argument schemas of the in-process graph's
own tools, so the prompts, the transcript and the dashboard see exactly what an
in-process run shows. The backend runs each call through the graph's tools node,
tool-call middleware included, against this run's sandbox, and hands back the
result and any other state the tool wrote.
"""

import asyncio
import logging
from typing import Final

import httpx2
from langchain.tools import ToolRuntime
from langchain_core.messages import ToolMessage, messages_from_dict
from langchain_core.tools import BaseTool, StructuredTool
from langgraph.config import get_config
from langgraph.types import Command
from pydantic import JsonValue

from rt.backend import BackendCallError, BackendTimeoutError, OpenSweBackend, ToolSpec, run_body

logger = logging.getLogger(__name__)

STATE_KEY: Final = "open_swe_state"
_TIMED_OUT: Final = (
    "Error: Open SWE did not answer this tool call in time. Whatever it started may "
    "still be running in the sandbox; check before running it again."
)
_CALL_FAILED: Final = (
    "Error: Open SWE could not complete this tool interaction. Execution may be uncertain; "
    "check the sandbox and any affected external state before retrying."
)


def run_id() -> str | None:
    """The Agent Server run this graph is executing, as the backend keys it."""
    config = get_config()
    configurable = config.get("configurable") or {}
    raw = configurable.get("run_id") or config.get("metadata", {}).get("run_id")
    return str(raw) if raw else None


def backend_tool(spec: ToolSpec, backend: OpenSweBackend) -> BaseTool:
    async def call(runtime: ToolRuntime, **arguments: JsonValue) -> Command:
        tool_call_id = runtime.tool_call_id or ""
        body = run_body(
            run_id(),
            runtime.state,
            name=spec.name,
            arguments=dict(arguments),
            tool_call_id=tool_call_id,
        )
        for attempt in range(3):
            try:
                reply = await backend.tool(body)
                break
            except BackendTimeoutError:
                # One stuck call fails that call, not the whole run.
                logger.warning("The backend did not answer a tool call", exc_info=True)
                message = ToolMessage(content=_TIMED_OUT, tool_call_id=tool_call_id, status="error")
                return Command(update={"messages": [message]})
            except BackendCallError as exc:
                cause = exc.__cause__
                if not (
                    isinstance(cause, httpx2.TransportError)
                    or isinstance(cause, httpx2.HTTPStatusError)
                    and cause.response.status_code in {502, 503, 504}
                ):
                    raise
                if isinstance(cause, httpx2.ConnectError) and attempt < 2:
                    logger.warning(
                        "The backend tool connection failed; retrying",
                        extra={"attempt": attempt + 1},
                    )
                    await asyncio.sleep(1 + 2 * attempt)
                    continue
                logger.warning(
                    "The backend could not complete a tool interaction",
                    extra={"error_type": type(cause).__name__},
                )
                message = ToolMessage(
                    content=_CALL_FAILED, tool_call_id=tool_call_id, status="error"
                )
                return Command(update={"messages": [message]})
        for event in reply.events:
            runtime.stream_writer(event)
        update: dict[str, object] = {
            "messages": messages_from_dict([reply.message, *reply.messages])
        }
        if reply.update:
            update[STATE_KEY] = reply.update
        return Command(update=update)

    return StructuredTool.from_function(
        coroutine=call,
        name=spec.name,
        description=spec.description,
        args_schema=spec.parameters,
    )
