"""The graph's tools, each one a call to the Open SWE backend.

They carry the names, descriptions and argument schemas of the in-process graph's
own tools, so the prompts, the transcript and the dashboard see exactly what an
in-process run shows. The backend runs each call through the graph's tools node,
tool-call middleware included, against this run's sandbox, and hands back the
result and any other state the tool wrote.
"""

from typing import Final

from langchain.tools import ToolRuntime
from langchain_core.messages import messages_from_dict
from langchain_core.tools import BaseTool, StructuredTool
from langgraph.config import get_config
from langgraph.types import Command
from pydantic import JsonValue

from rt.backend import OpenSweBackend, ToolSpec, run_body

STATE_KEY: Final = "open_swe_state"


def run_id() -> str | None:
    """The Agent Server run this graph is executing, as the backend keys it."""
    config = get_config()
    configurable = config.get("configurable") or {}
    raw = configurable.get("run_id") or config.get("metadata", {}).get("run_id")
    return str(raw) if raw else None


def backend_tool(spec: ToolSpec, backend: OpenSweBackend) -> BaseTool:
    async def call(runtime: ToolRuntime, **arguments: JsonValue) -> Command:
        reply = await backend.tool(
            run_body(
                run_id(),
                runtime.state,
                name=spec.name,
                arguments=dict(arguments),
                tool_call_id=runtime.tool_call_id or "",
            )
        )
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
