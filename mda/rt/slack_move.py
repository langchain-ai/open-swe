"""Moving a Slack conversation this deployment's Slack channel holds into a new Slack thread.

The run posts the new root through MDA with ``hand_off=True``, so the new Slack thread
takes the run's own thread, history and all: its replies, Stop and buttons reach it from
then on. The backend checks the move first and records where the thread went after,
both as the run (``slack_move``, ``slack_moved``). A move into another Slack channel stays
the backend's ``slack_move_thread``, which posts with the bot token, as before.

Two things move a thread: the agent's ``slack_move_thread`` tool, and a person's bare
``/breakout``, whose run the backend marks (``SLACK_MOVE_MARKER``) and which moves the
thread before any model call, then ends.
"""

import json
import logging
from collections.abc import Awaitable, Callable, Mapping
from functools import partial
from typing import Final

from langchain.agents.middleware.types import AgentMiddleware, AgentState, hook_config
from langchain.tools import ToolRuntime
from langchain_core.messages import AIMessage, HumanMessage, ToolCall, ToolMessage
from langchain_core.tools import BaseTool, StructuredTool
from langchain_core.utils.function_calling import convert_to_openai_tool
from langgraph.config import get_config
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.runtime import Runtime
from langgraph.types import Command
from managed_deepagents import ManagedThread, RuntimeChannel
from managed_deepagents._managed_thread import current_run_thread  # noqa: PLC2701
from pydantic import JsonValue
from runtime_api.endpoint import call
from runtime_api.slack import (
    SLACK_MOVE,
    SLACK_MOVE_MARKER,
    SLACK_MOVED,
    SlackMoved,
    SlackMoveRequest,
)

from rt.backend import OpenSweBackend
from rt.tools import run_id
from rt.tracing import MIDDLEWARE_TRACE_POLICY

logger = logging.getLogger(__name__)

MOVE_TOOL: Final = "slack_move_thread"


class SlackMoveMiddleware(AgentMiddleware):
    """Move a Slack conversation through MDA when it stays in its own Slack channel.

    Place it inside the transcript's reporting and outside ``RunToolsMiddleware``, which
    sends the tool to the backend otherwise.
    """

    trace_policy = MIDDLEWARE_TRACE_POLICY

    def __init__(self, backend: OpenSweBackend) -> None:
        self._backend = backend

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        call_ = request.tool_call
        if call_["name"] != MOVE_TOOL or _slack_channel(request.runtime) is None:
            return await handler(request)
        args = call_["args"]
        message = args.get("message")
        channel_id = args.get("channel_id")
        result = await self._move(
            request.runtime,
            message=message if isinstance(message, str) else "",
            channel_id=channel_id if isinstance(channel_id, str) and channel_id else None,
        )
        if result is None:
            # Another channel: the backend's tool moves it, as before.
            return await handler(request)
        return ToolMessage(
            content=json.dumps(result), tool_call_id=call_["id"] or "", name=MOVE_TOOL
        )

    def code_tool(self, tool: BaseTool) -> StructuredTool:
        """``tool`` as code mode calls it: through this middleware, as the model's own call.

        Code mode calls its tools directly, past every ``awrap_tool_call``; without this a
        move from code takes the backend's move, which hands no thread to the new Slack
        thread, so the conversation's later messages there can't run.
        """

        async def run(runtime: ToolRuntime, **arguments: JsonValue) -> ToolMessage | Command:
            call_ = ToolCall(
                name=tool.name, args=arguments, id=runtime.tool_call_id, type="tool_call"
            )
            request = ToolCallRequest(
                tool_call=call_, tool=tool, state=runtime.state, runtime=runtime
            )
            return await self.awrap_tool_call(request, partial(_invoked, tool))

        return StructuredTool.from_function(
            coroutine=run,
            name=tool.name,
            description=tool.description,
            args_schema=convert_to_openai_tool(tool)["function"]["parameters"],
        )

    @hook_config(can_jump_to=["end"])
    async def abefore_model(self, state: AgentState, runtime: Runtime) -> dict[str, object] | None:
        messages = state["messages"]
        last = messages[-1] if messages else None
        if not (
            isinstance(last, HumanMessage) and last.additional_kwargs.get(SLACK_MOVE_MARKER) is True
        ):
            return None
        channel = _slack_channel(runtime)
        post = channel.post if channel is not None else None
        if post is None:
            return None
        result = await self._move(runtime, message=None, channel_id=None)
        if result is None:
            text = "I can move this conversation only within this Slack channel for now."
        elif result.get("success") is True:
            text = "Moved this conversation to a new Slack thread."
        else:
            text = f"Could not move this thread: {result.get('error') or 'try again'}."
        if result is None or result.get("success") is not True:
            await post({"type": "content", "content": text})
        return {"messages": [AIMessage(content=text)], "jump_to": "end"}

    async def _move(
        self,
        runtime: object,
        *,
        message: str | None,
        channel_id: str | None,
    ) -> Mapping[str, JsonValue] | None:
        """The move's answer, or ``None`` when the backend moves it into another channel."""
        plan = await call(
            self._backend,
            SLACK_MOVE,
            SlackMoveRequest(run_id=run_id(), message=message, channel_id=channel_id),
        )
        if plan.result is not None:
            return plan.result
        if not plan.via_mda or not plan.root_text:
            return None
        channel = _slack_channel(runtime)
        if channel is None or channel.post is None:
            return None
        try:
            posted = await channel.post(
                {"type": "content", "content": plan.root_text}, new_thread=True, hand_off=True
            )
        except Exception as exc:  # noqa: BLE001 - the model or the person hears why
            logger.warning("Could not move a Slack conversation through MDA", exc_info=True)
            return {"success": False, "error": f"Slack post failed: {exc}"}
        moved = await call(
            self._backend,
            SLACK_MOVED,
            # The old Slack thread says where it went, as the agent's tool moves it too.
            SlackMoved(run_id=run_id(), thread_ts=posted["id"], mark_source=True),
        )
        return moved.result


async def _invoked(tool: BaseTool, request: ToolCallRequest) -> ToolMessage | Command:
    """``tool``'s own answer to ``request``, as the backend's move gives it."""
    call_id = request.tool_call.get("id") or ""
    result = await tool.ainvoke(
        ToolCall(
            name=tool.name,
            args={**request.tool_call["args"], "runtime": request.runtime},
            id=call_id,
            type="tool_call",
        )
    )
    if isinstance(result, ToolMessage | Command):
        return result
    return ToolMessage(content=str(result), tool_call_id=call_id, name=tool.name)


def _slack_channel(runtime: object) -> RuntimeChannel | None:
    """The run's Slack origin, when its thread is a conversation of MDA's Slack channel."""
    thread = getattr(runtime, "thread", None)
    if not isinstance(thread, ManagedThread) or not thread.id:
        thread = current_run_thread(runtime, get_config().get("configurable")).thread
    channel = thread.origin.channel if thread.origin is not None else None
    if channel is None or channel.provider != "slack" or channel.post is None:
        return None
    return channel
