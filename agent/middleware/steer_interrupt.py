"""Stop a running ``execute`` command when someone steers the run.

A steered message waits in the thread's queue for the next model call, which
never comes while a long command holds the run. Watching the queue alongside
the command lets the message cut the command short instead.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping

from langchain.agents.middleware.types import AgentState
from langchain_core.messages import ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.store.base import BaseStore
from langgraph.types import Command

from agent.middleware.trace import OpenSWEMiddleware
from agent.prompts import load_prompt
from agent.run_config import RunConfig
from agent.utils.thread_ops import STEER_FLAG

logger = logging.getLogger(__name__)

STEER_POLL_SECONDS = 2.0
_INTERRUPTIBLE_TOOLS = frozenset({"execute"})
_QUEUE_KEY = "pending_messages"


def _steer_ids(value: Mapping[str, object] | None) -> frozenset[str]:
    messages = value.get("messages") if value is not None else None
    if not isinstance(messages, list):
        return frozenset()
    ids: set[str] = set()
    for message in messages:
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, dict) or content.get(STEER_FLAG) is not True:
            continue
        queue_id = content.get("queue_id")
        if isinstance(queue_id, str):
            ids.add(queue_id)
    return frozenset(ids)


async def _queued_steers(store: BaseStore, thread_id: str) -> frozenset[str]:
    item = await store.aget(("queue", thread_id), _QUEUE_KEY)
    return _steer_ids(item.value if item is not None else None)


async def _wait_for_new_steer(store: BaseStore, thread_id: str, seen: frozenset[str]) -> None:
    while True:
        await asyncio.sleep(STEER_POLL_SECONDS)
        try:
            if await _queued_steers(store, thread_id) - seen:
                return
        except Exception:  # noqa: BLE001 - a missed poll only delays the interrupt
            logger.warning(
                "Could not check the thread queue for a steer",
                exc_info=True,
                extra={"agent_thread_id": thread_id},
            )


class SteerInterruptMiddleware(OpenSWEMiddleware):
    """Cancel an in-flight ``execute`` call once a new steer is queued.

    Only steers queued after the command started count: one already waiting
    stays queued until a model call consumes it, and a subagent never does.
    """

    state_schema = AgentState

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        store = request.runtime.store
        thread_id = RunConfig.from_config(request.runtime.config).thread_id
        if request.tool_call["name"] not in _INTERRUPTIBLE_TOOLS or store is None or not thread_id:
            return await handler(request)
        try:
            seen = await _queued_steers(store, thread_id)
        except Exception:  # noqa: BLE001 - the command still runs, just not interruptibly
            logger.warning(
                "Could not read the thread queue before a command",
                exc_info=True,
                extra={"agent_thread_id": thread_id},
            )
            return await handler(request)

        tool = asyncio.ensure_future(handler(request))
        steer = asyncio.ensure_future(_wait_for_new_steer(store, thread_id, seen))
        try:
            await asyncio.wait({tool, steer}, return_when=asyncio.FIRST_COMPLETED)
        except asyncio.CancelledError:
            tool.cancel()
            steer.cancel()
            await asyncio.wait({tool, steer})
            raise
        if tool.done():
            steer.cancel()
            return tool.result()
        tool.cancel()
        await asyncio.wait({tool})
        if not tool.cancelled():
            return tool.result()
        logger.info("Interrupted a command for a steer", extra={"agent_thread_id": thread_id})
        return ToolMessage(
            content=load_prompt("tools/execute-steered.md"),
            name=request.tool_call["name"],
            tool_call_id=request.tool_call["id"],
            status="error",
        )
