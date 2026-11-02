"""The review scout, as Managed Deep Agents runs play it."""

from functools import cache

from deepagents.backends.protocol import BackendProtocol
from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool

from openswe.agent_api.run_kinds import RunKind
from openswe.agent_api.tokens import RunSettings
from openswe.review_scout.graph import (
    PrepareReviewScoutRunMiddleware,
    review_scout_catalog,
    scout_sandbox,
    scout_tools,
)
from openswe.run_config import RunConfig
from openswe.sandboxes.tool_runtime import ToolSurface


@cache
def _tools_by_name() -> dict[str, BaseTool]:
    return {tool.name: tool for tool in scout_tools()}


def _thread_id(config: RunnableConfig) -> str:
    thread_id = RunConfig.from_config(config).thread_id
    if thread_id is None:
        raise ValueError("A scout run needs a thread")
    return thread_id


class ReviewScoutKind(RunKind):
    """The review scout's pieces."""

    assistant_id = "review-scout"
    reviews = True

    async def catalog(self, config: RunnableConfig, surface: ToolSurface) -> None:
        await review_scout_catalog(config, surface)

    async def preparation(
        self, config: RunnableConfig, settings: RunSettings, *, sandbox_id: str | None
    ) -> list[AgentMiddleware]:
        del settings, sandbox_id
        return [PrepareReviewScoutRunMiddleware(thread_id=_thread_id(config), config=config)]

    async def files(self, config: RunnableConfig) -> BackendProtocol:
        return scout_sandbox(_thread_id(config), RunConfig.from_config(config))

    async def tool(self, config: RunnableConfig, name: str) -> BaseTool | None:  # noqa: ARG002
        return _tools_by_name().get(name)
