"""The run's push and pull request guards, asked about a shell command before it runs.

The guards read a command, inspect the push it makes in the thread's box, and may
need a person's approval, so they stay on the backend. The MDA run asks them about
each ``execute`` it may let push or open a pull request, and the operations that run
commands, such as ``background_execute``, ask them before they start.
"""

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from functools import partial

from langchain.agents.middleware import AgentMiddleware
from langchain.tools import ToolRuntime
from langchain_core.messages import ToolCall, ToolMessage
from langgraph.config import get_config, get_stream_writer
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command
from pydantic import JsonValue

from openswe.middleware.pr_creation_guard import SHELL_TOOL_ARGS

Handler = Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]]


@dataclass(frozen=True)
class ShellDecision:
    """Whether a call may run, with the arguments to run it with, or why it may not."""

    arguments: Mapping[str, JsonValue] | None
    refusal: str | None = None


class ShellGuards:
    """The guards a planner put on the commands its run's tools run."""

    def __init__(self, guards: Sequence[AgentMiddleware]) -> None:
        self._guards = tuple(guards)

    @property
    def active(self) -> bool:
        return bool(self._guards)

    def guards(self, tool: str) -> bool:
        return bool(self._guards) and tool in SHELL_TOOL_ARGS

    async def decide(self, tool: str, arguments: Mapping[str, JsonValue]) -> ShellDecision:
        """What the guards make of ``tool`` running with ``arguments``.

        Runs inside the run's graph context, where the guards read its config.
        """
        if not self.guards(tool):
            return ShellDecision(arguments)
        allowed: list[ToolCallRequest] = []

        async def run(request: ToolCallRequest) -> ToolMessage:
            allowed.append(request)
            return ToolMessage(content="", tool_call_id=request.tool_call["id"] or "")

        handler: Handler = run
        for guard in reversed(self._guards):
            handler = partial(guard.awrap_tool_call, handler=handler)
        call_id = f"guard-{tool}"
        call = ToolCall(name=tool, args=dict(arguments), id=call_id, type="tool_call")
        runtime = ToolRuntime(
            state={},
            context=None,
            config=get_config(),
            stream_writer=get_stream_writer(),
            tool_call_id=call_id,
            store=None,
        )
        answer = await handler(
            ToolCallRequest(tool_call=call, tool=None, state={}, runtime=runtime)
        )
        if allowed:
            return ShellDecision(allowed[-1].tool_call["args"])
        refusal = answer.text if isinstance(answer, ToolMessage) else str(answer)
        return ShellDecision(None, refusal)
