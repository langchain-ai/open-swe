"""LangGraph entrypoint that fans cron ticks into fresh agent threads."""

from __future__ import annotations

import logging
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import RunnableConfig

from .dashboard.schedules import launch_scheduled_agent_run, normalize_cron_schedule
from .delivery_auto import delivery_auto_tick
from .delivery_queue import delivery_queue_poll
from .reconcile import reconcile_stale_runs
from .utils.thread_ops import langgraph_client

logger = logging.getLogger(__name__)

DELIVERY_AUTO_CRON_NAMESPACE: list[str] = ["delivery_auto_cron"]
DELIVERY_AUTO_CRON_KEY = "auto-mode"
DELIVERY_QUEUE_POLLING_CRON_NAMESPACE: list[str] = ["delivery_queue_polling_cron"]
DELIVERY_QUEUE_POLLING_CRON_KEY = "linear"
DEFAULT_DELIVERY_AUTO_SCHEDULE = "*/5 * * * *"
DEFAULT_DELIVERY_QUEUE_POLL_SCHEDULE = "*/5 * * * *"
_SCHEDULER_ASSISTANT_ID = "scheduler"


class SchedulerState(TypedDict, total=False):
    schedule_id: str
    task: str
    result: dict[str, Any]


def _client():
    return langgraph_client()


def _value_from_item(item: Any) -> dict[str, Any] | None:
    if item is None:
        return None
    value = item.get("value") if isinstance(item, dict) else getattr(item, "value", None)
    return value if isinstance(value, dict) else None


async def get_delivery_queue_polling_cron() -> dict[str, Any] | None:
    item = await _client().store.get_item(
        DELIVERY_QUEUE_POLLING_CRON_NAMESPACE,
        DELIVERY_QUEUE_POLLING_CRON_KEY,
    )
    return _value_from_item(item)


async def get_delivery_auto_cron() -> dict[str, Any] | None:
    item = await _client().store.get_item(
        DELIVERY_AUTO_CRON_NAMESPACE,
        DELIVERY_AUTO_CRON_KEY,
    )
    return _value_from_item(item)


async def _ensure_scheduler_cron(
    *,
    namespace: list[str],
    key: str,
    schedule: str,
    task: str,
    kind: str,
) -> dict[str, Any]:
    normalized_schedule = normalize_cron_schedule(schedule)
    existing = _value_from_item(await _client().store.get_item(namespace, key))
    if (
        existing
        and existing.get("schedule") == normalized_schedule
        and isinstance(existing.get("cron_id"), str)
        and existing["cron_id"]
    ):
        return existing

    cron = await _client().crons.create(
        _SCHEDULER_ASSISTANT_ID,
        schedule=normalized_schedule,
        input={"task": task},
        config={"configurable": {"task": task}},
        metadata={"kind": kind, "task": task},
    )
    cron_id = cron.get("cron_id") if isinstance(cron, dict) else getattr(cron, "cron_id", None)
    if not isinstance(cron_id, str) or not cron_id:
        raise RuntimeError(f"{task} cron creation did not return a cron_id")
    record = {
        "id": key,
        "cron_id": cron_id,
        "schedule": normalized_schedule,
        "task": task,
        "enabled": True,
    }
    await _client().store.put_item(namespace, key, record)
    return record


async def ensure_delivery_queue_polling_cron(
    schedule: str = DEFAULT_DELIVERY_QUEUE_POLL_SCHEDULE,
) -> dict[str, Any]:
    return await _ensure_scheduler_cron(
        namespace=DELIVERY_QUEUE_POLLING_CRON_NAMESPACE,
        key=DELIVERY_QUEUE_POLLING_CRON_KEY,
        schedule=schedule,
        task="delivery_queue_poll",
        kind="delivery_queue_poll",
    )


async def ensure_delivery_auto_cron(
    schedule: str = DEFAULT_DELIVERY_AUTO_SCHEDULE,
) -> dict[str, Any]:
    return await _ensure_scheduler_cron(
        namespace=DELIVERY_AUTO_CRON_NAMESPACE,
        key=DELIVERY_AUTO_CRON_KEY,
        schedule=schedule,
        task="delivery_auto_tick",
        kind="delivery_auto_tick",
    )


async def _launch(state: SchedulerState, config: RunnableConfig) -> dict[str, Any]:
    configurable = config.get("configurable") or {}
    task = state.get("task") or configurable.get("task")
    if task == "reconcile":
        return {"result": await reconcile_stale_runs()}
    if task == "delivery_queue_poll":
        return {"result": await delivery_queue_poll()}
    if task == "delivery_auto_tick":
        return {"result": await delivery_auto_tick()}
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
