"""LangGraph entrypoint that fans cron ticks into their handlers."""

from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import RunnableConfig

from .scheduling.tasks import run_scheduler_task


class SchedulerState(TypedDict, total=False):
    task: str
    payload: dict[str, Any]
    result: dict[str, Any]


async def _dispatch(state: SchedulerState) -> dict[str, Any]:
    payload = state.get("payload")
    return {
        "result": await run_scheduler_task(
            state.get("task"), payload if isinstance(payload, dict) else {}
        )
    }


def get_scheduler(config: RunnableConfig | None = None):
    builder = StateGraph(SchedulerState)
    builder.add_node("dispatch", _dispatch)
    builder.add_edge(START, "dispatch")
    builder.add_edge("dispatch", END)
    return builder.compile().with_config(config or {})
