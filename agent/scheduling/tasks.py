"""What a scheduler tick can be, and what running one does.

One entry per task kind: the payload type its producer builds, and the handler
that consumes it. The graph in :mod:`agent.graphs.scheduler` only forwards
``(task, payload)`` here, so adding a kind never touches the graph.

:func:`normalize_scheduler_input` is the one place a tick's wire shape is read,
so the pre-migration shapes are understood without a second dispatch path.
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

# The payload fields a pre-migration producer flattened next to ``task`` in the
# run input, mirroring them into ``config.configurable``. A cron registered
# before the ``{"task", "payload"}`` contract still fires that shape and cannot
# be rewritten in place, so the graph goes on reading it.
_LEGACY_PAYLOAD_FIELDS: dict[str, tuple[str, ...]] = {
    WATCH_TASK: ("watch_key",),
    BACKGROUND_TASKS_TASK: ("thread_id",),
    SESSION_COST_TASK: (
        "agent_thread_id",
        "run_id",
        "prepare_run_id",
        "channel_id",
        "thread_ts",
        "attempt",
    ),
    RECONCILE_TASK: (),
    SCHEDULE_TASK: ("schedule_id",),
}


def _legacy_value(input: Mapping[str, Any], configurable: Mapping[str, Any], field: str) -> Any:
    for source in (input, configurable):
        value = source.get(field)
        if value is not None and value != "":
            return value
    return None


def normalize_scheduler_input(
    input: Mapping[str, Any], configurable: Mapping[str, Any]
) -> tuple[str | None, dict[str, Any]]:
    """Read one tick as ``(task, payload)``, current shape or pre-migration.

    Current producers send ``{"task": kind, "payload": {...}}`` and nothing else
    is consulted. A cron or delayed run created before that contract sends the
    task at the top level — or omits it, which meant an agent schedule — with
    the payload's fields flattened beside it and mirrored into
    ``config.configurable``; those are mapped onto the same ``(task, payload)``.
    """
    task = input.get("task")
    payload = input.get("payload")
    if isinstance(task, str) and task and isinstance(payload, dict):
        return task, dict(payload)
    if not (isinstance(task, str) and task):
        task = configurable.get("task")
    if not (isinstance(task, str) and task):
        has_schedule_id = _legacy_value(input, configurable, "schedule_id") is not None
        task = SCHEDULE_TASK if has_schedule_id else None
    if task is None:
        return None, {}
    return task, {
        field: value
        for field in _LEGACY_PAYLOAD_FIELDS.get(task, ())
        if (value := _legacy_value(input, configurable, field)) is not None
    }


async def run_scheduler_task(task: str | None, payload: Mapping[str, Any]) -> dict[str, Any]:
    handler = HANDLERS.get(task) if isinstance(task, str) else None
    if handler is None:
        logger.warning("Scheduler tick for unknown task %r", task)
        return {"status": "unknown_task", "task": task}
    return await handler(payload)
