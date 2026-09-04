"""After-agent middleware that persists run usage telemetry."""

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware, AgentState, ModelRequest, ModelResponse
from langchain_core.messages import AIMessage
from langgraph.runtime import Runtime

from agent.agent_cost import schedule_agent_cost_refresh
from agent.dashboard.agent_usage import (
    agent_run_needs_cost_refresh,
    mark_agent_cost_refresh_scheduled,
    record_agent_run_completion,
)
from agent.run_config import RunConfig
from agent.utils.run_usage import summarize_run_usage

logger = logging.getLogger(__name__)


async def finalize_agent_run_usage(
    *, run_id: str, thread_id: str, state: dict[str, Any] | None
) -> None:
    """Persist terminal run usage and schedule deferred cost enrichment."""
    try:
        recorded = await record_agent_run_completion(
            run_id=run_id,
            usage=summarize_run_usage(state, run_id=run_id),
        )
        if not recorded and not await agent_run_needs_cost_refresh(run_id=run_id):
            return
        scheduled = await schedule_agent_cost_refresh({"thread_id": thread_id, "run_id": run_id})
        if scheduled:
            await mark_agent_cost_refresh_scheduled(run_id=run_id)
    except Exception:  # noqa: BLE001
        logger.debug(
            "Failed to record completed agent usage",
            extra={"usage_run_id": run_id, "usage_thread_id": thread_id},
            exc_info=True,
        )


class RecordRunUsageMiddleware(AgentMiddleware):
    """Tag model responses with their run and persist usage on completion."""

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        response = await handler(request)
        run_id = RunConfig.from_runtime().prepare_run_id
        if run_id:
            for message in response.result:
                if isinstance(message, AIMessage):
                    message.response_metadata = {
                        **message.response_metadata,
                        "open_swe_run_id": run_id,
                    }
        return response

    async def aafter_agent(self, state: AgentState, runtime: Runtime) -> dict[str, Any] | None:
        del runtime
        cfg = RunConfig.from_runtime()
        if not cfg.prepare_run_id or not cfg.thread_id:
            return None
        await finalize_agent_run_usage(
            run_id=cfg.prepare_run_id,
            thread_id=cfg.thread_id,
            state=dict(state),
        )
        return None


record_run_usage = RecordRunUsageMiddleware()
