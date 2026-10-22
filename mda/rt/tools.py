"""The graph's tools, each one a call to the Open SWE backend.

They carry the names, descriptions and argument schemas of the in-process graph's
own tools, so the prompts, the transcript and the dashboard see exactly what an
in-process run shows. The backend runs each call through the graph's tools node,
tool-call middleware included, against this run's sandbox, and hands back the
result and any other state the tool wrote. The file and shell tools of a box MDA
owns run here instead (``rt.sandbox_tools``), apart from the calls only the
backend can settle.
"""

import logging
from collections.abc import Mapping
from typing import Final

from langchain.tools import ToolRuntime
from langchain_core.messages import ToolMessage, messages_from_dict
from langchain_core.tools import StructuredTool
from langgraph.config import get_config
from langgraph.types import Command
from pydantic import JsonValue

from rt.backend import (
    BackendTimeoutError,
    BackendUnavailableError,
    OpenSweBackend,
    ToolSpec,
    run_body,
)
from rt.tracing import TurnTrace

logger = logging.getLogger(__name__)

STATE_KEY: Final = "open_swe_state"
_TIMED_OUT: Final = (
    "Error: Open SWE did not answer this tool call in time. Whatever it started may "
    "still be running in the sandbox; check before running it again."
)


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
    """A tool call as the backend takes it, with the turn's git evidence, which its trace holds."""
    body = run_body(
        run_id(), state, name=name, arguments=dict(arguments), tool_call_id=tool_call_id
    )
    held = body.get("state")
    body["state"] = {**(held if isinstance(held, dict) else {}), **TurnTrace.current_evidence()}
    return body


async def run_on_backend(
    backend: OpenSweBackend, name: str, arguments: Mapping[str, JsonValue], runtime: ToolRuntime
) -> Command:
    """Run one call of the graph's tool ``name`` on the backend, through its tools node."""
    tool_call_id = runtime.tool_call_id or ""
    body = tool_body(runtime.state, name=name, arguments=arguments, tool_call_id=tool_call_id)
    try:
        reply = await backend.tool(body)
    except BackendTimeoutError:
        logger.warning("The backend did not answer a tool call", exc_info=True)
        message = ToolMessage(content=_TIMED_OUT, tool_call_id=tool_call_id, status="error")
        return Command(update={"messages": [message]})
    except BackendUnavailableError:
        logger.warning("The backend could not complete a tool call")
        message = ToolMessage(
            content=(
                "Error: The Open SWE backend could not be reached for this tool call. "
                "It may or may not have run; check state before retrying."
            ),
            tool_call_id=tool_call_id,
            status="error",
        )
        return Command(update={"messages": [message]})
    for event in reply.events:
        runtime.stream_writer(event)
    update: dict[str, object] = {"messages": messages_from_dict([reply.message, *reply.messages])}
    if reply.update:
        TurnTrace.record_evidence(reply.update)
        update[STATE_KEY] = reply.update
    return Command(update=update)


def backend_tool(spec: ToolSpec, backend: OpenSweBackend) -> StructuredTool:
    async def call(runtime: ToolRuntime, **arguments: JsonValue) -> Command:
        return await run_on_backend(backend, spec.name, arguments, runtime)

    return StructuredTool.from_function(
        coroutine=call,
        name=spec.name,
        description=spec.description,
        args_schema=spec.parameters,
    )
