"""Top-level guard that converts unhandled graph-update errors into a closeout.

When the graph fans out (e.g. many parallel ``task``/tool steps) two nodes can
write the same state key in one superstep. If that key lacks a fan-in reducer,
LangGraph raises ``InvalidUpdateError`` (``INVALID_CONCURRENT_GRAPH_UPDATE``)
and the whole run aborts to status=error with empty output. State keys should
carry reducers so this never happens, but this guard is the belt-and-suspenders
layer: it catches the error, posts a closeout to the source channel so the
thread is never left silently abandoned, and ends the turn with a visible
message instead of an abrupt crash.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.messages import AIMessage
from langgraph.config import get_config
from langgraph.errors import InvalidUpdateError

from ..utils.source_notify import notify_source_channel

logger = logging.getLogger(__name__)

GRAPH_UPDATE_FAILURE_MESSAGE = (
    "I hit an internal concurrency error while coordinating parallel work and had "
    "to stop. Your task was not completed. This is a transient framework issue — "
    "please retrigger and I'll pick it back up."
)


class InvalidUpdateGuardMiddleware(AgentMiddleware):
    """Catch InvalidUpdateError from concurrent graph updates and close out cleanly."""

    async def _closeout(self) -> None:
        try:
            await notify_source_channel(get_config(), GRAPH_UPDATE_FAILURE_MESSAGE)
        except Exception:
            logger.exception("Failed to post graph-update-error closeout to source channel")

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> Any:
        try:
            return await handler(request)
        except InvalidUpdateError:
            logger.exception("Concurrent graph update aborted the run; posting closeout")
            await self._closeout()
            return AIMessage(content=GRAPH_UPDATE_FAILURE_MESSAGE)
