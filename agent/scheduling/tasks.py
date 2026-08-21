"""What a scheduler tick can be, and what running one does.

One entry per task kind: the payload type its producer builds, and the handler
that consumes it. The graph in :mod:`agent.graphs.scheduler` only forwards
``(task, payload)`` here, so adding a kind never touches the graph.
"""

import logging
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from ..baby_sit import WATCH_TASK, BabySitPayload, evaluate_watch
from ..background_tasks import (
    BACKGROUND_TASKS_TASK,
    BackgroundTasksPayload,
    monitor_background_tasks,
)
from ..reconcile import RECONCILE_TASK, ReconcilePayload, reconcile_stale_runs
from ..session_cost import SESSION_COST_TASK, SessionCostPayload, run_session_cost_refresh
from .agent_schedules import SCHEDULE_TASK, SchedulePayload, launch_scheduled_agent_run

logger = logging.getLogger(__name__)

# What a producer may put in a tick's run input, one alternative per task kind.
SchedulerPayload = (
    BabySitPayload
    | BackgroundTasksPayload
    | SessionCostPayload
    | SchedulePayload
    | ReconcilePayload
)
Handler = Callable[[Mapping[str, Any]], Awaitable[dict[str, Any]]]


def _text(payload: Mapping[str, Any], field: str) -> str | None:
    value = payload.get(field)
    return value if isinstance(value, str) and value else None


async def _run_baby_sit(payload: Mapping[str, Any]) -> dict[str, Any]:
    key = _text(payload, "watch_key")
    if key is None:
        return {"status": "missing_watch_key"}
    return {"status": await evaluate_watch(key)}


async def _run_background_tasks(payload: Mapping[str, Any]) -> dict[str, Any]:
    thread_id = _text(payload, "thread_id")
    if thread_id is None:
        return {"status": "missing_thread_id"}
    return await monitor_background_tasks(thread_id)


async def _run_session_cost(payload: Mapping[str, Any]) -> dict[str, Any]:
    return await run_session_cost_refresh(payload)


async def _run_reconcile(_payload: Mapping[str, Any]) -> dict[str, Any]:
    return await reconcile_stale_runs()


async def _run_schedule(payload: Mapping[str, Any]) -> dict[str, Any]:
    schedule_id = _text(payload, "schedule_id")
    if schedule_id is None:
        logger.warning("Scheduled agent tick missing schedule_id")
        return {"status": "missing_schedule_id"}
    return await launch_scheduled_agent_run(schedule_id)


HANDLERS: dict[str, Handler] = {
    WATCH_TASK: _run_baby_sit,
    BACKGROUND_TASKS_TASK: _run_background_tasks,
    SESSION_COST_TASK: _run_session_cost,
    RECONCILE_TASK: _run_reconcile,
    SCHEDULE_TASK: _run_schedule,
}


async def run_scheduler_task(task: str | None, payload: Mapping[str, Any]) -> dict[str, Any]:
    handler = HANDLERS.get(task) if isinstance(task, str) else None
    if handler is None:
        logger.warning("Scheduler tick for unknown task %r", task)
        return {"status": "unknown_task", "task": task}
    return await handler(payload)
