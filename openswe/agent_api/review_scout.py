"""The review scout, as Managed Deep Agents runs play it."""

from typing import Final

from langchain_core.runnables import RunnableConfig
from runtime_api.catalog import LoopRules, RunModels

from openswe.agent_api.graph import RemoteGraph
from openswe.agent_api.loop import RunLoop
from openswe.middleware.exclude_tools import ExcludeToolsMiddleware
from openswe.review_scout.graph import PrepareReviewScoutRunMiddleware, plan_review_scout
from openswe.run_config import RunConfig
from openswe.sandboxes.tool_runtime import ToolSurface

ASSISTANT_ID: Final = "review-scout"


async def _plan(config: RunnableConfig, surface: ToolSurface) -> None:
    await plan_review_scout(config, surface)


async def _loop(config: RunnableConfig, rules: LoopRules, models: RunModels) -> RunLoop | None:
    """The scout's hooks around each model call, in the order its plan has them."""
    thread_id = RunConfig.from_config(config).thread_id
    if thread_id is None:
        return None
    return RunLoop(
        [
            # Its preparation stops a scout with nothing to plan, and shapes each call.
            PrepareReviewScoutRunMiddleware(thread_id=thread_id, config=config),
            ExcludeToolsMiddleware(excluded=frozenset(rules.hidden_tools)),
        ],
        models,
    )


REVIEW_SCOUT: Final = RemoteGraph(ASSISTANT_ID, _plan, loop=_loop)
