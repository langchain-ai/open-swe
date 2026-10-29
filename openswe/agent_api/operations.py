"""The work an MDA run's tools ask the backend to do, one operation each.

An operation is one of the tools the run's planner offers, run as a plain call:
its arguments, the run state it reads and the request's id. There is no tools node
and no tool-call middleware: the tool call around it, with its result message,
errors, transcript and checks, is the MDA run's.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Annotated, cast, get_args, get_origin, get_type_hints

from langchain.tools import ToolRuntime
from langchain_core.messages import ToolCall, ToolMessage
from langchain_core.tools import BaseTool
from langgraph.config import get_config, get_stream_writer
from langgraph.prebuilt import InjectedState
from langgraph.types import Command
from pydantic import JsonValue


def _marker[T](annotation: object, kind: type[T]) -> T | type[T] | None:
    """``kind``, or the instance of it, ``annotation`` is marked with."""
    if isinstance(annotation, kind):
        return annotation
    if isinstance(annotation, type) and issubclass(annotation, kind):
        return annotation
    origin = get_origin(annotation)
    if origin is Annotated or get_args(annotation):
        for argument in get_args(annotation):
            if (found := _marker(argument, kind)) is not None:
                return found
    if isinstance(origin, type) and issubclass(origin, kind):
        return origin
    return None


@dataclass(frozen=True)
class Injections:
    """What a tool's own signature asks to be handed besides its arguments."""

    # Argument name to the state key it reads, or None for the whole state.
    state: Mapping[str, str | None]
    # The argument that takes the tool runtime, if any.
    runtime: str | None = None

    @classmethod
    def of(cls, tool: BaseTool) -> Injections:
        function = getattr(tool, "coroutine", None) or getattr(tool, "func", None)
        hints = get_type_hints(function, include_extras=True) if function is not None else {}
        state: dict[str, str | None] = {}
        runtime: str | None = None
        for name, hint in hints.items():
            if (marker := _marker(hint, InjectedState)) is not None:
                state[name] = marker.field if isinstance(marker, InjectedState) else None
            elif _marker(hint, ToolRuntime) is not None:
                runtime = name
        return cls(state, runtime)

    @property
    def reads_all(self) -> bool:
        return self.runtime is not None or any(key is None for key in self.state.values())

    @property
    def reads(self) -> list[str]:
        return sorted({key for key in self.state.values() if key is not None})


async def invoke(
    tool: BaseTool,
    arguments: Mapping[str, JsonValue],
    state: Mapping[str, object],
    request_id: str,
) -> ToolMessage | Command:
    """Run ``tool`` once with ``arguments``, handing it what its signature injects.

    Runs inside the run's graph context, which ``get_config`` and the stream writer
    read from.
    """
    injections = Injections.of(tool)
    supplied: dict[str, object] = dict(arguments)
    for name, key in injections.state.items():
        supplied[name] = dict(state) if key is None else state.get(key)
    if injections.runtime is not None:
        supplied[injections.runtime] = ToolRuntime(
            state=dict(state),
            context=None,
            config=get_config(),
            stream_writer=get_stream_writer(),
            tool_call_id=request_id,
            store=None,
        )
    call = ToolCall(name=tool.name, args=supplied, id=request_id, type="tool_call")
    result = await tool.ainvoke(call)
    if isinstance(result, ToolMessage | Command):
        return result
    return ToolMessage(content=cast(str, result), tool_call_id=request_id, name=tool.name)
