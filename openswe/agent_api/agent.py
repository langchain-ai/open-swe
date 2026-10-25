"""The coding agent, as Managed Deep Agents runs play it."""

from typing import Final

from langchain_core.runnables import RunnableConfig

from openswe.agent_api.graph import RemoteGraph
from openswe.middleware.record_run_usage import record_run_usage
from openswe.middleware.transcript import TranscriptMiddleware
from openswe.sandboxes.tool_runtime import ToolSurface
from openswe.skill_store.run_skills import RunSkillStores

ASSISTANT_ID: Final = "agent"


async def _plan(config: RunnableConfig, surface: ToolSurface) -> None:
    from openswe.server import plan_agent

    await plan_agent(config, surface)


AGENT: Final = RemoteGraph(
    ASSISTANT_ID,
    _plan,
    skills=RunSkillStores.for_run,
    reporters=(TranscriptMiddleware(), record_run_usage),
)
