"""Durable per-channel incident graph."""

from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel

from agent.incidents.worker import process_channel


class IncidentState(BaseModel):
    incident_id: str = ""
    result: dict[str, Any] | None = None


async def _process(state: IncidentState) -> dict[str, Any]:
    return {"result": await process_channel(state.incident_id)}


def get_incidents(config: RunnableConfig | None = None):
    graph = StateGraph(IncidentState)
    graph.add_node("incidents", _process)
    graph.add_edge(START, "incidents")
    graph.add_edge("incidents", END)
    return graph.compile().with_config(config or {})
