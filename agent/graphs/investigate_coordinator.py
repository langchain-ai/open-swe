"""One durable, enqueued admission coordinator per deployment."""

from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel

from agent.investigations.coordinator import coordinate


class CoordinatorInput(BaseModel):
    result: dict[str, Any] | None = None


async def _coordinate(state: CoordinatorInput) -> dict[str, Any]:
    return {"result": await coordinate()}


def get_coordinator(config: RunnableConfig | None = None):
    graph = StateGraph(CoordinatorInput)
    graph.add_node("coordinate", _coordinate)
    graph.add_edge(START, "coordinate")
    graph.add_edge("coordinate", END)
    return graph.compile().with_config(config or {})
