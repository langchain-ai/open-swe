"""LangGraph entrypoint that fans cron ticks into fresh agent threads."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import RunnableConfig

from .dashboard.schedules import launch_scheduled_agent_run
from .reconcile import reconcile_auto_merge_prs, reconcile_reviewer_heads, reconcile_stale_runs

logger = logging.getLogger(__name__)


class SchedulerState(TypedDict, total=False):
    schedule_id: str
    task: str
    result: dict[str, Any]


async def _launch(state: SchedulerState, config: RunnableConfig) -> dict[str, Any]:
    configurable = config.get("configurable") or {}
    task = state.get("task") or configurable.get("task")
    if task == "reconcile":
        stale_runs, auto_merge, reviewer_heads = await asyncio.gather(
            reconcile_stale_runs(), reconcile_auto_merge_prs(), reconcile_reviewer_heads()
        )
        return {
            "result": {
                "stale_runs": stale_runs,
                "auto_merge": auto_merge,
                "reviewer_heads": reviewer_heads,
            }
        }
    schedule_id = state.get("schedule_id") or configurable.get("schedule_id")
    if not isinstance(schedule_id, str) or not schedule_id:
        logger.warning("Scheduled agent tick missing schedule_id")
        return {"result": {"status": "missing_schedule_id"}}
    return {"result": await launch_scheduled_agent_run(schedule_id)}


def get_scheduler(config: RunnableConfig | None = None):
    builder = StateGraph(SchedulerState)
    builder.add_node("launch", _launch)
    builder.add_edge(START, "launch")
    builder.add_edge("launch", END)
    return builder.compile().with_config(config or {})
