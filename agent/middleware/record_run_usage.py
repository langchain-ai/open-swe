"""After-agent middleware that persists run usage telemetry."""

import logging
from typing import Any

from langchain.agents.middleware import AgentState, after_agent
from langgraph.runtime import Runtime

from agent.agent_cost import schedule_agent_cost_refresh
from agent.dashboard.agent_usage import record_agent_run_completion
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
            usage=summarize_run_usage(state),
        )
        if recorded:
            await schedule_agent_cost_refresh({"thread_id": thread_id, "run_id": run_id})
    except Exception:  # noqa: BLE001
        logger.debug(
            "Failed to record completed agent usage",
            extra={"usage_run_id": run_id, "usage_thread_id": thread_id},
            exc_info=True,
        )


@after_agent
async def record_run_usage(state: AgentState, runtime: Runtime) -> dict[str, Any] | None:
    """Persist completed-run tokens and schedule deferred cost enrichment."""
    del runtime
    cfg = RunConfig.from_runtime()
    if not cfg.prepare_run_id:
        return None
    await finalize_agent_run_usage(
        run_id=cfg.prepare_run_id,
        thread_id=cfg.thread_id,
        state=dict(state),
    )
    return None
