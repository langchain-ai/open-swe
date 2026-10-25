"""The review scout, as Managed Deep Agents runs play it."""

from typing import Final

from langchain_core.runnables import RunnableConfig

from openswe.agent_api.graph import RemoteGraph
from openswe.review_scout.graph import plan_review_scout
from openswe.sandboxes.tool_runtime import ToolSurface

ASSISTANT_ID: Final = "review-scout"


async def _plan(config: RunnableConfig, surface: ToolSurface) -> None:
    await plan_review_scout(config, surface)


REVIEW_SCOUT: Final = RemoteGraph(ASSISTANT_ID, _plan)
