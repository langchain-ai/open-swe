"""LangGraph entrypoint that fans cron ticks into their handlers."""

from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import RunnableConfig

from ..scheduling.tasks import normalize_scheduler_input, run_scheduler_task


class SchedulerState(TypedDict, total=False):
    """The tick's channels: ``(task, payload)`` plus the legacy flat fields.

    The legacy fields are declared because LangGraph drops undeclared input
    keys: without them a pre-migration cron's payload would never reach
    :func:`normalize_scheduler_input`. Nothing writes them.
    """

    task: str
    payload: dict[str, Any]
    result: dict[str, Any]
    schedule_id: str
    watch_key: str
    thread_id: str
    agent_thread_id: str
    run_id: str
    prepare_run_id: str
    channel_id: str
    thread_ts: str
    attempt: int


async def _dispatch(state: SchedulerState, config: RunnableConfig) -> dict[str, Any]:
    task, payload = normalize_scheduler_input(state, config.get("configurable") or {})
    return {"result": await run_scheduler_task(task, payload)}


def get_scheduler(config: RunnableConfig | None = None):
    builder = StateGraph(SchedulerState)
    builder.add_node("dispatch", _dispatch)
    builder.add_edge(START, "dispatch")
    builder.add_edge("dispatch", END)
    return builder.compile().with_config(config or {})
