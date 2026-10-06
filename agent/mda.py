"""Forward agent runs to the Managed Deep Agents deployment built from ``mda/``."""

from dataclasses import dataclass, field
from typing import Annotated

from langchain_core.messages import AnyMessage
from langgraph.graph import START, StateGraph, add_messages
from langgraph.graph.state import CompiledStateGraph
from langgraph.pregel.remote import RemoteGraph

from agent.config import ENV
from agent.dashboard.workspace_settings_cache import cached_workspace_settings

MDA_ASSISTANT_ID = "open-swe"


@dataclass
class MdaAgentState:
    messages: Annotated[list[AnyMessage], add_messages] = field(default_factory=list)


async def mda_agent_enabled(workspace: str | None) -> bool:
    if not ENV.MDA_AGENT_URL.optional():
        return False
    return (await cached_workspace_settings(workspace)).mda_agent_enabled


def build_mda_agent() -> CompiledStateGraph:
    """A one-node graph whose node runs the remote MDA agent on the same thread ID."""
    remote = RemoteGraph(
        MDA_ASSISTANT_ID,
        url=ENV.MDA_AGENT_URL.get(),
        api_key=ENV.MDA_AGENT_API_KEY.optional(),
        distributed_tracing=True,
    )
    return StateGraph(MdaAgentState).add_node("agent", remote).add_edge(START, "agent").compile()
