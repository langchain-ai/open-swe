"""The pull request reviewer, as Managed Deep Agents runs play it."""

from typing import Final

from langchain_core.runnables import RunnableConfig
from runtime_api.catalog import LoopRules, RunModels

from openswe.agent_api.graph import RemoteGraph
from openswe.agent_api.loop import RunLoop
from openswe.middleware.exclude_tools import ExcludeToolsMiddleware
from openswe.middleware.prepare_run import RenderedSystemPromptMiddleware
from openswe.middleware.refresh_github_proxy import refresh_github_proxy_before_model
from openswe.review.diff import compute_diff_line_set
from openswe.reviewer import plan_reviewer
from openswe.sandboxes.tool_runtime import ToolSurface

ASSISTANT_ID: Final = "reviewer"


async def _plan(config: RunnableConfig, surface: ToolSurface) -> None:
    await plan_reviewer(config, surface)


def _restore(state: dict[str, object]) -> None:
    # Sets do not cross to MDA and back; the diff they index does.
    diff_text = state.get("diff_text")
    if isinstance(diff_text, str) and diff_text and "diff_line_set" not in state:
        state["diff_line_set"] = compute_diff_line_set(diff_text)


async def _loop(_config: RunnableConfig, rules: LoopRules, models: RunModels) -> RunLoop:
    """The reviewer's hooks around each model call, in the order its plan has them."""
    return RunLoop(
        [
            RenderedSystemPromptMiddleware(),
            ExcludeToolsMiddleware(excluded=frozenset(rules.hidden_tools)),
            refresh_github_proxy_before_model,
        ],
        models,
    )


REVIEWER: Final = RemoteGraph(ASSISTANT_ID, _plan, restore_state=_restore, loop=_loop)
