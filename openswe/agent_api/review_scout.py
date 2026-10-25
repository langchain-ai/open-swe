"""The review scout, as Managed Deep Agents runs play it."""

from collections.abc import Sequence
from functools import cache
from typing import Final, cast

from deepagents.backends.protocol import BackendProtocol
from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from runtime_api.catalog import LoopRules, RunModels

from openswe.agent_api.graph import RemoteGraph
from openswe.agent_api.loop import RunLoop
from openswe.agent_api.run_parts import RunParts
from openswe.middleware.exclude_tools import ExcludeToolsMiddleware
from openswe.review_scout.graph import (
    FinishPlanMiddleware,
    PrepareReviewScoutRunMiddleware,
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

    async def loop(self, config: RunnableConfig, rules: LoopRules, models: RunModels) -> RunLoop:
        """The scout's hooks around each model call, in the order its plan has them."""
        return RunLoop(
            [
                # Its preparation stops a scout with nothing to plan, and shapes each call.
                PrepareReviewScoutRunMiddleware(thread_id=_thread_id(config), config=config),
                ExcludeToolsMiddleware(excluded=frozenset(rules.hidden_tools)),
            ],
            models,
        )

    def end_hooks(self, config: RunnableConfig) -> Sequence[AgentMiddleware]:
        # Middleware types are invariant in the state they extend; a run merges them all.
        finish = FinishPlanMiddleware(thread_id=_thread_id(config), config=config)
        return (cast(AgentMiddleware, finish),)


REVIEW_SCOUT: Final = RemoteGraph(ASSISTANT_ID, plan_review_scout, ReviewScoutParts())
