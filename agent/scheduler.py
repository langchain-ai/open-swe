"""LangGraph entrypoint that fans cron ticks into fresh agent threads."""

import logging
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import RunnableConfig

from agent.baby_sit import evaluate_watch
from agent.background_tasks import CRON_KIND as BACKGROUND_TASK_CRON_KIND
from agent.background_tasks import monitor_background_tasks
from agent.dashboard.schedules import launch_scheduled_agent_run
from agent.reconcile import reconcile_stale_runs
from agent.run_config import RunConfig
from agent.session_cost import run_session_cost_refresh

logger = logging.getLogger(__name__)


class SchedulerState(TypedDict, total=False):
    schedule_id: str
    task: str
    watch_key: str
    thread_id: str
    agent_thread_id: str
    run_id: str
    prepare_run_id: str
    channel_id: str
    thread_ts: str
    attempt: int
    result: dict[str, Any]


async def _launch(state: SchedulerState, config: RunnableConfig) -> dict[str, Any]:
    cfg = RunConfig.from_config(config)
    task = state.get("task") or cfg.task
    if task == "reconcile":
        return {"result": await reconcile_stale_runs()}
    if task == "baby_sit":
        key = state.get("watch_key") or cfg.watch_key
        if not key:
            return {"result": {"status": "missing_watch_key"}}
        return {"result": {"status": await evaluate_watch(key)}}
    if task == BACKGROUND_TASK_CRON_KIND:
        thread_id = state.get("thread_id") or cfg.thread_id
        if not thread_id:
            return {"result": {"status": "missing_thread_id"}}
        return {"result": await monitor_background_tasks(thread_id)}
    if task == "session_cost":
        return {"result": await run_session_cost_refresh(state)}
    schedule_id = state.get("schedule_id") or cfg.schedule_id
    if not schedule_id:
        logger.warning("Scheduled agent tick missing schedule_id")
        return {"result": {"status": "missing_schedule_id"}}
    return {"result": await launch_scheduled_agent_run(schedule_id)}


def get_scheduler(config: RunnableConfig | None = None):
    builder = StateGraph(SchedulerState)
    builder.add_node("launch", _launch)
    builder.add_edge(START, "launch")
    builder.add_edge("launch", END)
    return builder.compile().with_config(config or {})
