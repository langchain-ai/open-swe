"""The coding agent, as Managed Deep Agents runs play it."""

from typing import Final

from langchain_core.runnables import RunnableConfig

from openswe.agent_api.graph import RemoteGraph
from openswe.sandboxes.tool_runtime import ToolSurface

ASSISTANT_ID: Final = "agent"


async def _plan(config: RunnableConfig, surface: ToolSurface) -> None:
    from openswe.server import plan_agent

    await plan_agent(config, surface)


AGENT: Final = RemoteGraph(ASSISTANT_ID, _plan)
