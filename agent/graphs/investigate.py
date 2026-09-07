"""Durable per-channel investigation graph."""

from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel

from agent.investigations.worker import process_channel


class InvestigationState(BaseModel):
    investigation_id: str = ""
    result: dict[str, Any] | None = None


async def _process(state: InvestigationState) -> dict[str, Any]:
    return {"result": await process_channel(state.investigation_id)}


def get_investigate(config: RunnableConfig | None = None):
    graph = StateGraph(InvestigationState)
    graph.add_node("investigate", _process)
    graph.add_edge(START, "investigate")
    graph.add_edge("investigate", END)
    return graph.compile().with_config(config or {})
