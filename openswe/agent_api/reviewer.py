"""The pull request reviewer, as Managed Deep Agents runs play it."""

from collections.abc import Sequence
from functools import cache
from typing import Final

from deepagents.backends.protocol import BackendProtocol
from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from runtime_api.catalog import LoopRules, RunModels

from openswe.agent_api.graph import RemoteGraph
from openswe.agent_api.loop import RunLoop
from openswe.agent_api.run_parts import RunParts
from openswe.github.thread_token import get_github_token
from openswe.middleware.exclude_tools import ExcludeToolsMiddleware
from openswe.middleware.prepare_run import RenderedSystemPromptMiddleware
from openswe.middleware.refresh_github_proxy import refresh_github_proxy_before_model
from openswe.middleware.settle_review_check import settle_review_check_on_exit
from openswe.review.diff import compute_diff_line_set
from openswe.reviewer import (
    ensure_reviewer_github_token,
    plan_reviewer,
    reviewer_box,
    reviewer_tools,
)
from openswe.run_config import RunConfig

ASSISTANT_ID: Final = "reviewer"


@cache
def _tools_by_name() -> dict[str, BaseTool]:
    return {tool.name: tool for tool in reviewer_tools()}


class ReviewerParts(RunParts):
    """The reviewer's pieces, as its plan builds them, for a call answered without it."""

    def restore(self, state: dict[str, object]) -> None:
        # Sets do not cross to MDA and back; the diff they index does.
        diff_text = state.get("diff_text")
        if isinstance(diff_text, str) and diff_text and "diff_line_set" not in state:
            state["diff_line_set"] = compute_diff_line_set(diff_text)

    async def credentials(self, config: RunnableConfig) -> None:
        cfg = RunConfig.from_config(config)
        if cfg.thread_id is not None and get_github_token(config) is None:
            await ensure_reviewer_github_token(cfg.thread_id, cfg)

    async def files(self, config: RunnableConfig) -> BackendProtocol | None:
        cfg = RunConfig.from_config(config)
        return None if cfg.thread_id is None else reviewer_box(cfg.thread_id, cfg)

    async def tool(self, config: RunnableConfig, name: str) -> BaseTool | None:  # noqa: ARG002
        return _tools_by_name().get(name)

    async def loop(
        self,
        config: RunnableConfig,  # noqa: ARG002
        rules: LoopRules,
        models: RunModels,
    ) -> RunLoop:
        """The reviewer's hooks around each model call, in the order its plan has them."""
        return RunLoop(
            [
                RenderedSystemPromptMiddleware(),
                ExcludeToolsMiddleware(excluded=frozenset(rules.hidden_tools)),
                refresh_github_proxy_before_model,
            ],
            models,
        )

    def end_hooks(self, config: RunnableConfig) -> Sequence[AgentMiddleware]:  # noqa: ARG002
        return (settle_review_check_on_exit,)


REVIEWER: Final = RemoteGraph(ASSISTANT_ID, plan_reviewer, ReviewerParts())
