"""The work an MDA run's tools ask the backend to do, one operation each.

An operation is one of the tools the run's planner offers, run as a plain call:
its arguments, the run state it reads and the request's id. There is no tools node
and no tool-call middleware: the tool call around it, with its result message,
errors, transcript and checks, is the MDA run's.
"""

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Annotated, cast, get_args, get_origin, get_type_hints

from langchain.tools import ToolRuntime
from langchain_core.messages import ToolCall, ToolMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from langgraph.config import get_config, get_stream_writer
from langgraph.errors import NodeCancelledError
from langgraph.prebuilt import InjectedState
from langgraph.runtime import Runtime
from langgraph.types import Command
from pydantic import JsonValue, TypeAdapter
from runtime_api.operations import OperationResult, RunOperation

from openswe.agent_api.connections import carrying
from openswe.agent_api.failures import OperationFailure
from openswe.agent_api.git_metadata import collect_pr_targets
from openswe.agent_api.run_state import json_state, jsonable
from openswe.agent_api.runs import in_graph_context
from openswe.agent_api.shell_guards import ShellGuards
from openswe.middleware.client_tools import ClientToolsMiddleware
from openswe.sandboxes.tool_runtime import tool_parameters

_json = TypeAdapter(JsonValue)


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


class Operation:
    """One call of a run's tool, ``tool``, as ``request`` asks: its arguments in, its result out."""

    def __init__(self, tool: BaseTool | None, request: RunOperation) -> None:
        self.tool = tool
        self.request = request

    def _rejection(self, tool: BaseTool) -> str | None:
        """Why the call's arguments don't fit ``tool``'s schema, naming what it accepts instead."""
        schema = tool_parameters(tool)
        additional = schema.get("additionalProperties")
        if additional is True or isinstance(additional, Mapping):
            return None
        raw_properties = schema.get("properties")
        properties = sorted(raw_properties) if isinstance(raw_properties, Mapping) else []
        unexpected = sorted(set(self.request.arguments) - set(properties))
        if not unexpected:
            return None
        raw_required = schema.get("required")
        required = set(raw_required) if isinstance(raw_required, list) else set()
        accepted = ", ".join(f"{key}{'*' if key in required else ''}" for key in properties)
        return (
            f"Unexpected arguments for {self.request.name}: {', '.join(unexpected)}. "
            f"Accepted parameters: {accepted or '(none)'} (* = required)"
        )

    def _result(self, outcome: ToolMessage | Command) -> OperationResult:
        """What the tool's own result says, as the operation's result."""
        request_id = self.request.request_id
        state: Mapping[str, object] = {}
        message = outcome
        if isinstance(outcome, Command):
            update = outcome.update if isinstance(outcome.update, Mapping) else {}
            messages = update.get("messages")
            message = next(
                (
                    m
                    for m in (messages if isinstance(messages, list) else [])
                    if isinstance(m, ToolMessage) and m.tool_call_id == request_id
                ),
                ToolMessage(content="", tool_call_id=request_id),
            )
            state = {key: value for key, value in update.items() if key != "messages"}
        if not isinstance(message, ToolMessage):
            raise TypeError("An operation answered with no result")
        return OperationResult(
            content=_json.validate_python(message.content),
            status=message.status,
            artifact=jsonable(message.artifact),
            message_id=message.id,
            state=json_state(state),
        )

    async def run(
        self,
        *,
        config: RunnableConfig,
        state: dict[str, object],
        guards: ShellGuards,
        client: ClientToolsMiddleware | None,
        ready: Callable[[Runtime], Awaitable[None]],
        notify: bool,
    ) -> OperationResult:
        """Do the call as the run ``config`` names, once ``ready``; ``notify`` tells the person
        of a failure that needs them."""
        request = self.request
        tool = self.tool
        if tool is None:
            return OperationResult(
                content=f"{request.name} is not one of this run's operations", status="error"
            )
        if rejection := self._rejection(tool):
            return OperationResult(content=rejection, status="error")
        if client is not None and (
            pending := client.pending_result(request.name, request.request_id)
        ):
            return self._result(pending)
        events: list[object] = []

        async def run(runtime: Runtime) -> OperationResult:
            await ready(runtime)
            decision = await guards.decide(request.name, request.arguments)
            if decision.arguments is None:
                return OperationResult(content=decision.refusal or "", status="error")
            with collect_pr_targets() as pull_requests, carrying(request.credentials):
                try:
                    outcome = await invoke(tool, decision.arguments, state, request.request_id)
                except NodeCancelledError:
                    # The run is being cancelled: that ends the call, not just its result.
                    raise
                except Exception as exc:
                    failure = await OperationFailure.of(
                        exc, operation=request.name, config=config, notify=notify
                    )
                    return OperationResult(
                        content=failure.content,
                        status="error",
                        sandbox_unreachable=failure.sandbox_unreachable,
                    )
            result = self._result(outcome)
            return result.model_copy(update={"pull_requests": pull_requests})

        result = await in_graph_context({**config, "run_name": request.name}, run, events=events)
        return result.model_copy(update={"events": [jsonable(e) for e in events]})
