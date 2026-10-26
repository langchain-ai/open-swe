"""The review scout, as Managed Deep Agents runs play it."""

from functools import cache
from typing import Final

from deepagents.backends.protocol import BackendProtocol
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool

from openswe.agent_api.graph import RemoteGraph
from openswe.agent_api.run_parts import RunParts
from openswe.review_scout.graph import (
    plan_review_scout,
    scout_box,
    scout_tools,
)
from openswe.run_config import RunConfig

ASSISTANT_ID: Final = "review-scout"


@cache
def _tools_by_name() -> dict[str, BaseTool]:
    return {tool.name: tool for tool in scout_tools()}


def _thread_id(config: RunnableConfig) -> str:
    thread_id = RunConfig.from_config(config).thread_id
    if thread_id is None:
        raise ValueError("A scout run needs a thread")
    return thread_id


class ReviewScoutParts(RunParts):
    """The scout's pieces, as its plan builds them, for a call answered without it."""

    async def files(self, config: RunnableConfig) -> BackendProtocol:
        return scout_box(_thread_id(config), RunConfig.from_config(config))

    async def tool(self, config: RunnableConfig, name: str) -> BaseTool | None:  # noqa: ARG002
        return _tools_by_name().get(name)


REVIEW_SCOUT: Final = RemoteGraph(ASSISTANT_ID, plan_review_scout, ReviewScoutParts())
