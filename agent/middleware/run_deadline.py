"""One checkpointed deadline shared by model calls, retries, and tool calls."""

import asyncio
import logging
import math
import time
from collections.abc import Awaitable, Callable, Mapping
from typing import NotRequired
from uuid import uuid4

from langchain.agents.middleware.types import (
    AgentState,
    ModelRequest,
    ModelResponse,
    ToolCallRequest,
)
from langchain_core.messages import ToolMessage
from langgraph.runtime import Runtime
from langgraph.types import Command

from agent.config import ENV
from agent.middleware.trace import OpenSWEMiddleware
from agent.prompts import load_prompt

logger = logging.getLogger(__name__)


class RunDeadlineExceeded(RuntimeError):
    """Terminal budget exhaustion, deliberately excluded from provider retries."""


class RunDeadlineState(AgentState):
    run_deadline_at: NotRequired[float]
    run_deadline_for: NotRequired[str]


def _configured_timeout() -> float:
    raw = ENV.OPEN_SWE_RUN_TIMEOUT_SECONDS.get("3600")
    try:
        value = float(raw)
        if math.isfinite(value) and value > 0:
            return value
    except ValueError:
        logger.warning("Invalid run timeout", extra={"configured": raw})
    raise ValueError("OPEN_SWE_RUN_TIMEOUT_SECONDS must be positive and finite")


class RunDeadlineMiddleware(OpenSWEMiddleware[RunDeadlineState]):
    state_schema = RunDeadlineState

    def __init__(
        self, *, invocation_id: str | None = None, timeout_seconds: float | None = None
    ) -> None:
        self._invocation_id = invocation_id
        self._timeout = timeout_seconds if timeout_seconds is not None else _configured_timeout()
        if not math.isfinite(self._timeout) or self._timeout <= 0:
            raise ValueError("run timeout must be positive and finite")
        self._deadline: float | None = None

    async def abefore_agent(self, state: RunDeadlineState, runtime: Runtime) -> dict[str, object]:
        runtime_run_id = (
            runtime.execution_info.run_id if runtime.execution_info is not None else None
        )
        identity = self._invocation_id or runtime_run_id or str(uuid4())
        existing = state.get("run_deadline_at")
        self._deadline = (
            existing
            if state.get("run_deadline_for") == identity and existing is not None
            else time.time() + self._timeout
        )
        return {"run_deadline_for": identity, "run_deadline_at": self._deadline}

    async def _call[T](self, handler: Callable[[], Awaitable[T]], state: object = None) -> T:
        # A resumed graph can skip its checkpointed before-agent nodes.
        if self._deadline is None and isinstance(state, Mapping):
            stored = state.get("run_deadline_at")
            if (
                isinstance(stored, (float, int))
                and not isinstance(stored, bool)
                and math.isfinite(stored)
            ):
                self._deadline = float(stored)
        if self._deadline is None:
            logger.info("Initializing execution budget for a legacy checkpoint")
            self._deadline = time.time() + self._timeout
        remaining = self._deadline - time.time()
        if remaining <= 0:
            raise RunDeadlineExceeded(load_prompt("run-deadline.md"))
        timer = asyncio.timeout(remaining)
        try:
            async with timer:
                return await handler()
        except TimeoutError as exc:
            if not timer.expired():
                raise
            logger.warning("Run deadline exceeded")
            raise RunDeadlineExceeded(load_prompt("run-deadline.md")) from exc

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        return await self._call(lambda: handler(request), request.state)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        return await self._call(lambda: handler(request), request.state)
