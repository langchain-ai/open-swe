from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, NotRequired

from langchain.agents.middleware.types import (
    AgentMiddleware,
    AgentState,
    ModelRequest,
    ModelResponse,
)
from langchain_core.messages import SystemMessage
from langgraph.runtime import Runtime


class PrepareRunState(AgentState):
    run_prepared: NotRequired[bool]
    work_dir: NotRequired[str | None]
    rendered_system_prompt: NotRequired[str | None]


class BasePrepareRunMiddleware(AgentMiddleware):
    """Checkpointed per-run setup.

    Subclasses must keep `_prepare` idempotent. LangGraph checkpoints the
    `run_prepared` latch after this before-agent node, so resumed runs skip
    completed setup; if a run fails before that checkpoint, setup may execute
    again and every operation it calls must tolerate that.
    """

    state_schema = PrepareRunState

    async def abefore_agent(
        self,
        state: PrepareRunState,
        runtime: Runtime,
    ) -> dict[str, Any] | None:
        if state.get("run_prepared"):
            return None
        updates = await self._prepare(state, runtime)
        return {"run_prepared": True, **updates}

    async def _prepare(self, state: PrepareRunState, runtime: Runtime) -> dict[str, Any]:
        raise NotImplementedError

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        rendered = request.state.get("rendered_system_prompt")
        if isinstance(rendered, str) and rendered:
            existing = request.system_message.text if request.system_message is not None else ""
            content = f"{rendered}\n\n{existing}" if existing else rendered
            request = request.override(system_message=SystemMessage(content=content))
        return await handler(request)
